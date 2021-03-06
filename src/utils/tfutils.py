import tensorflow as tf
from typing import Dict, Optional, List, Callable, Union, Tuple
from collections import namedtuple
from functools import partial

from utils.constants import SMALL_NUMBER, BIG_NUMBER


def get_optimizer(name: str, learning_rate: float, learning_rate_decay: float, global_step: tf.Variable, decay_steps: int = 100000, momentum: Optional[float] = None):
    momentum = momentum if momentum is not None else 0.0
    name = name.lower()

    scheduled_learning_rate = tf.train.exponential_decay(learning_rate=learning_rate,
                                                         global_step=global_step,
                                                         decay_steps=decay_steps,
                                                         decay_rate=learning_rate_decay)
    if name == 'sgd':
        return tf.train.GradientDescentOptimizer(learning_rate=scheduled_learning_rate)
    elif name == 'nesterov':
        return tf.train.MomentumOptimizer(learning_rate=scheduled_learning_rate, momentum=momentum)
    elif name == 'adagrad':
        return tf.train.AdagradOptimizer(learning_rate=scheduled_learning_rate)
    elif name == 'adam':
        return tf.train.AdamOptimizer(learning_rate=scheduled_learning_rate)
    else:
        raise ValueError(f'Unknown optimizer {name}!')


def get_activation(fn_name: Optional[str]) -> Optional[Callable[[tf.Tensor], tf.Tensor]]:
    """
    Returns the activation function with the given name.
    """
    if fn_name is None:
        return None

    fn_name = fn_name.lower()
    if fn_name == 'tanh':
        return tf.nn.tanh
    elif fn_name == 'relu':
        return tf.nn.relu
    elif fn_name == 'sigmoid':
        return tf.math.sigmoid
    elif fn_name == 'leaky_relu':
        return partial(tf.nn.leaky_relu, alpha=0.25)
    elif fn_name == 'elu':
        return tf.nn.elu
    elif fn_name == 'crelu':
        return tf.nn.crelu
    elif fn_name == 'linear':
        return None
    else:
        raise ValueError(f'Unknown activation name {fn_name}.')


def get_regularizer(name: Optional[str], scale: float) -> Optional[Callable[[tf.Tensor], tf.Tensor]]:
    """
    Returns a weight regularizer with the given name and scale.
    """
    if name is None:
        return None

    name = name.lower()
    if name in ('l1', 'lasso'):
        return tf.contrib.layers.l1_regularizer(scale=scale)
    elif name == 'l2':
        return tf.contrib.layers.l2_regularizer(scale=scale)
    elif name == 'none':
        return None
    else:
        raise ValueError(f'Unknown regularization name: {name}')


def apply_noise(x: tf.Tensor, scale: Union[float, tf.Tensor]) -> tf.Tensor:
    """
    Applies unbiased Gaussian noise with the given scale to the given tensor.
    """
    noise = scale * tf.random.normal(shape=tf.shape(x), mean=0.0, stddev=1.0)
    return x + noise


def mask_last_element(values: tf.Tensor) -> tf.Tensor:
    """
    Sets the final element of each sequence to zero.

    Args:
        values: A [B, T] tensor of scalar values for each batch element (B) and sequence (T)
    Returns:
        A [B, T] tensor in which the final element of each sequence (T - 1) is set to zero
    """
    seq_length = tf.shape(values)[1]
    indices = tf.range(start=0, limit=seq_length)  # [T]
    mask = tf.expand_dims(tf.cast(indices < seq_length - 1, dtype=tf.float32), axis=0)  # [1, T]
    return values * mask


def pool_predictions(pred: tf.Tensor, states: tf.Tensor, seq_length: int, W: tf.Variable, b: tf.Variable, activation_noise: tf.Tensor, name: str) -> tf.Tensor:
    """
    Pools the given predictions using (trainable) weighted averages derived from the states.

    Args:
        pred: [B, L, K] tensor of predictions for each class
        states: [B, L, D] tensor of final states from each sequence element
        seq_length: The sequence length (L)
        W: [D * 2, 1] tensor containing the transformation weights
        b: [1, 1] tensor containing the transformation bias
        activation_noise: The noise to apply to the transformation result
        name: Name prefix to use for trainable variables.
    Returns:
        [B, L, K] of pooled log probabilities.
    """
    # Create results array to hold L tensors, each of dimension [B, K]
    results = tf.TensorArray(size=seq_length, dtype=tf.float32, clear_after_read=True, name='{0}-results'.format(name))
    weightsArray = tf.TensorArray(size=seq_length, dtype=tf.float32, clear_after_read=True, name='{0}-weights'.format(name))

    def body(index: tf.Tensor, pred: tf.Tensor, states: tf.Tensor, results_array: tf.TensorArray, weightsArray: tf.TensorArray):
        current_state = tf.gather(states, index, axis=1)  # [B, D]
        current_state = tf.tile(tf.expand_dims(current_state, axis=1), multiples=(1, seq_length, 1))  # [B, L, D]

        states_concat = tf.concat([states, current_state], axis=-1)  # [B, L, 2 * D]

        weights = tf.matmul(states_concat, W) + b  # [B, L, 1]
        weights = apply_noise(weights, scale=activation_noise)

        index_mask = tf.cast(tf.less_equal(tf.range(start=0, limit=seq_length), index), tf.float32)  # [L]
        index_mask = (1.0 - index_mask) * BIG_NUMBER  # [L]
        index_mask = tf.reshape(index_mask, (1, -1, 1))  # [1, L, 1]

        masked_weights = weights - index_mask  # [B, L, 1]
        masked_weights = tf.squeeze(masked_weights, axis=-1)  # [B, L]

        normalized_weights = tf.contrib.sparsemax.sparsemax(masked_weights)  # [B, L]
        weightsArray = weightsArray.write(value=masked_weights, index=index)

        normalized_weights = tf.expand_dims(normalized_weights, axis=-1)  # [B, L, 1]

        pooled = tf.reduce_sum(pred * normalized_weights, axis=1)  # [B, K]

        results_array = results_array.write(value=pooled, index=index)

        return [index + 1, pred, states, results_array, weightsArray]

    def cond(index, _1, _2, _3, _4):
        return index < seq_length

    # Execute the while loop
    index = tf.constant(1, dtype=tf.int32)
    _, _, _, pooled_results, agg_weights = tf.while_loop(cond=cond,
                                                         body=body,
                                                         loop_vars=[index, pred, states, results, weightsArray],
                                                         maximum_iterations=seq_length,
                                                         name=name)

    # Store the first sequence element into the results array. There is no pooling here because there are no "previous" elements.
    pooled_results = pooled_results.write(value=tf.gather(pred, 0, axis=1), index=0)

    results = pooled_results.stack()  # [L, B, D]
    return tf.transpose(results, perm=[1, 0, 2]), tf.transpose(agg_weights.stack(), perm=[1, 0, 2])  # [B, L, D]


def successive_pooling(inputs: tf.Tensor, aggregation_weights: tf.Tensor, seq_length: int, name: str) -> tf.Tensor:
    """
    Successively pools the input tensor over the time dimension.

    Args:
        inputs: A [B, T, D] tensor of input vectors of dimension (D) for each time step (T) and batch sample (B)
        aggregation_weights: A [B, T, 1] tensor of aggregation weights. These should be un-normalized.
        seq_length: A integer containing the sequence length (T)
        name: Name of this layer
    Returns:
        A [B, T, D] tensor containing the successively-pooled outputs.
    """
    # Create results array
    results = tf.TensorArray(size=seq_length, dtype=tf.float32, clear_after_read=True, name='{0}-results'.format(name))

    # Loop body function
    def body(index: tf.Tensor, inputs: tf.Tensor, aggregation_weights: tf.Tensor, results_array: tf.TensorArray):
        index_mask = tf.cast(tf.less_equal(tf.range(start=0, limit=seq_length), index), tf.float32)  # [T]
        index_mask = tf.reshape(index_mask, (1, -1, 1))  # [1, T, 1]

        masked_weights = aggregation_weights * index_mask  # [B, T, 1]
        normalizing_factor = tf.reduce_sum(masked_weights, axis=1, keepdims=True)  # [B, 1, 1]
        normalized_weights = masked_weights / (tf.maximum(normalizing_factor, SMALL_NUMBER))  # [B, T, 1]

        weighted_inputs = inputs * normalized_weights  # [B, T, D]
        pooled_inputs = tf.reduce_sum(weighted_inputs, axis=1)  # [B, D]

        # Write the pooled result to the array
        results_array = results_array.write(value=pooled_inputs, index=index)

        return [index + 1, inputs, aggregation_weights, results_array]

    # Stop Condition Function
    def stop_condition(index, _1, _2, _3):
        return index < seq_length

    # Execute the while loop
    index = tf.constant(0, dtype=tf.int32)
    _, _, _, pooled_results = tf.while_loop(cond=stop_condition,
                                            body=body,
                                            loop_vars=[index, inputs, aggregation_weights, results],
                                            maximum_iterations=seq_length,
                                            name=name)

    results = pooled_results.stack()  # [T, B, D]
    return tf.transpose(results, perm=[1, 0, 2])  # [B, T, D]


def variables_for_loss_op(variables: List[tf.Variable], loss_op: str) -> List[tf.Variable]:
    """
    Gets all variables that have a gradient with respect to the given loss operation.

    Args:
        variables: List of trainable variables
        loss_op: Operation to compute gradient for
    Returns:
        A list of all variables with an existing gradient
    """
    gradients = tf.gradients(loss_op, variables)
    return [v for g, v in zip(gradients, variables) if g is not None]
