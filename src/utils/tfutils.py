import tensorflow as tf
from typing import Dict, Optional, List, Callable, Union, Tuple
from collections import namedtuple
from functools import partial

from utils.constants import SMALL_NUMBER


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


def majority_vote(logits: tf.Tensor) -> tf.Tensor:
    """
    Outputs a prediction based on a majority-voting scheme.

    Args:
        logits: A [B, T, D] tensor containing the output logits for each sequence element (T)
    Returns:
        A [B] tensor containing the predictions for each batch sample (D)
    """
    predicted_probs = tf.nn.softmax(logits, axis=-1)  # [B, T, D]
    predicted_classes = tf.argmax(predicted_probs, axis=-1)  # [B, T]

    batch_size, seq_length = tf.shape(predicted_probs)[0], tf.shape(predicted_probs)[1]
    sample_classes = tf.TensorArray(size=batch_size, dtype=tf.int32, clear_after_read=True, name='predictions')

    seq_length = tf.shape(predicted_classes)[-1]

    def body(index, predictions_array):
        sample_classes = tf.gather(predicted_classes, index)  # [T]

        label_counts = tf.bincount(tf.cast(sample_classes, dtype=tf.int32))  # [T]
        predicted_label = tf.cast(tf.argmax(label_counts), dtype=tf.int32)

        predictions_array = predictions_array.write(index=index, value=predicted_label)
        return [index + 1, predictions_array]

    def cond(index, _):
        return index < batch_size

    i = tf.constant(0)
    _, predictions_array = tf.while_loop(cond=cond, body=body,
                                         loop_vars=[i, sample_classes],
                                         parallel_iterations=1,
                                         maximum_iterations=batch_size,
                                         name='majority-while-loop')
    return predictions_array.stack()


def make_tf_rnn_cell(cell_type: str, num_units: int, activation: str, layers: int, name_prefix: Optional[str]) -> tf.nn.rnn_cell.MultiRNNCell:

    def make_cell(cell_type: str, num_units: int, activation: str, name: str):
        if cell_type == 'vanilla':
            return tf.nn.rnn_cell.BasicRNNCell(num_units=num_units,
                                               activation=get_activation(activation),
                                               name=name)
        elif cell_type == 'gru':
            return tf.nn.rnn_cell.GRUCell(num_units=num_units,
                                          activation=get_activation(activation),
                                          kernel_initializer=tf.glorot_uniform_initializer(),
                                          bias_initializer=tf.random_uniform_initializer(minval=-0.7, maxval=0.7),
                                          name=name)
        elif cell_type == 'lstm':
            return tf.nn.rnn_cell.LSTMCell(num_units=num_units,
                                           activation=get_activation(activation),
                                           initializer=tf.glorot_uniform_initializer(),
                                           name=name)
        elif cell_type == 'ugrnn':
            return tf.contrib.rnn.UGRNNCell(num_units=num_units,
                                            initializer=tf.glorot_uniform_initializer())

        raise ValueError(f'Unknown cell type: {cell_type}')

    cell_type = cell_type.lower()
    cells: List[tf.rnn_cell.RNNCell] = []
    name_prefix = f'{name_prefix}-cell' if name_prefix is not None else 'cell'
    for i in range(layers):
        name = f'{name_prefix}-{i}'
        cell = make_cell(cell_type, num_units, activation, name)
        cells.append(cell)

    return tf.nn.rnn_cell.MultiRNNCell(cells)


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
