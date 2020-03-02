import tensorflow as tf
from typing import Tuple, Dict, Optional, Any, List
from dpu_utils.tfutils import get_activation


def make_rnn_cell(cell_type: str,
                  input_units: int,
                  output_units: int,
                  activation: str,
                  dropout_keep_rate: tf.Tensor,
                  name: str,
                  num_layers: Optional[int] = None,
                  use_skip_connections: bool = False):
    if num_layers is None:
        return make_single_rnn_cell(cell_type, input_units, output_units, activation, dropout_keep_rate, name, use_skip_connections)

    return MultiRNNCell(num_layers=num_layers,
                        input_units=input_units,
                        output_units=output_units,
                        activation=activation,
                        dropout_keep_prob=dropout_keep_rate,
                        cell_type=cell_type,
                        name=name,
                        use_skip_connections=use_skip_connections)


def make_single_rnn_cell(cell_type: str,
                         input_units: int,
                         output_units: int,
                         activation: str,
                         dropout_keep_rate: tf.Tensor,
                         name: str,
                         use_skip_connections: bool = False):
    cell_type = cell_type.lower()

    if cell_type == 'gru':
        return GRU(input_units, output_units, activation, dropout_keep_rate, name, use_skip_connections)
    if cell_type == 'vanilla':
        return VanillaCell(input_units, output_units, activation, dropout_keep_rate, name, use_skip_connections)
    if cell_type == 'lstm':
        return LSTM(input_units, output_units, activation, dropout_keep_rate, name, use_skip_connections)
    raise ValueError(f'Unknown cell name {cell_type}!')


class RNNCell:

    def __init__(self,
                 input_units: int,
                 output_units: int,
                 activation: str,
                 dropout_keep_prob: tf.Tensor,
                 name: str,
                 use_skip_connections: bool = False,
                 state_size: Optional[int] = None):
        """
        Initializes the RNN Cell

        Args:
            input_units: Number of dimensions of the input vectors
            output_units: Number of dimensions of the output vectors
            activation: Name of the activation function (i.e. tanh)
            dropout_keep_prob: Dropout keep rate for gate values
            name: Name of the RNN Cell
            use_skip_connections: Whether to allow skip connections through this cell
            state_size: Size of the state. Defaults to output_units
        """
        self.input_units = input_units
        self.output_units = output_units
        self.activation = get_activation(activation)
        self.dropout_keep_prob = dropout_keep_prob
        self.initializer = tf.initializers.glorot_uniform()
        self.state_size = output_units if state_size is None else state_size
        self.use_skip_connections = use_skip_connections
        self.name = name
        self.init_weights()

    @property
    def num_state_elements(self) -> int:
        """
        Logical number of vectors which comprise the state.
        """
        return 1

    def init_weights(self):
        """
        Initializes the trainable variables
        """
        pass

    def __call__(self, inputs: tf.Tensor,
                 state: tf.Tensor,
                 skip_input: Optional[tf.Tensor] = None) -> Tuple[tf.Tensor, tf.Tensor, List[tf.Tensor]]:
        """
        Executes the RNN cell.

        Args:
            inputs: Input vectors [B, D] tensor
            state: State vectors [B, S] tensor
            skip_input: Optional skip connections, [B, S] tensor if provided
        Returns:
            A tuple of (output, state, list of gate values)
        """
        pass

    def zero_state(self, batch_size: tf.Tensor, dtype: Any) -> tf.Tensor:
        state = tf.fill(dims=[batch_size, self.state_size], value=0, name=f'{self.name}-initial-state')
        return tf.cast(state, dtype=dtype)


class MultiRNNCell(RNNCell):

    def __init__(self,
                 num_layers: int,
                 input_units: int,
                 output_units: int,
                 activation: str,
                 dropout_keep_prob: tf.Tensor,
                 name: str,
                 cell_type: str,
                 use_skip_connections: bool = False,
                 state_size: Optional[int] = None):
        assert num_layers >= 1, 'Must provide at least one layer'
        super().__init__(input_units, output_units, activation, dropout_keep_prob, name, use_skip_connections, state_size)
        self.num_layers = num_layers

        self.cells: List[RNNCell] = []
        for i in range(num_layers):
            cell = make_single_rnn_cell(cell_type=cell_type,
                                        input_units=input_units if i == 0 else output_units,
                                        output_units=output_units,
                                        activation=activation,
                                        dropout_keep_rate=dropout_keep_prob,
                                        name=f'{name}-cell-{i}',
                                        use_skip_connections=use_skip_connections)
            self.cells.append(cell)

    @property
    def num_state_elements(self) -> int:
        return self.cells[0].num_state_elements

    def zero_state(self, batch_size: tf.Tensor, dtype: Any) -> List[tf.Tensor]:
        return [cell.zero_state(batch_size, dtype) for cell in self.cells]

    def __call__(self, inputs: tf.Tensor,
                 state: List[tf.Tensor],
                 skip_input: Optional[List[tf.Tensor]] = None) -> Tuple[tf.Tensor, List[tf.Tensor], List[List[tf.Tensor]]]:
        assert len(self.cells) == len(state), 'The number of states must be equal to the number of cells'

        cell_gates: List[List[tf.Tensor]] = []
        cell_states: List[tf.Tensor] = []
        cell_outputs: List[tf.Tensor] = [inputs]

        for i, (cell, state) in enumerate(zip(self.cells, state)):
            skip_connection = skip_input[i] if skip_input is not None else None

            cell_output, cell_state, cell_gate = cell(inputs=cell_outputs[-1],
                                                      state=state,
                                                      skip_input=skip_connection)
            cell_outputs.append(cell_output)
            cell_states.append(cell_state)
            cell_gates.append(cell_gate)

        final_output = cell_outputs[-1]
        return final_output, cell_states, cell_gates


class GRU(RNNCell):

    def init_weights(self):
        self.W_update = tf.get_variable(name=f'{self.name}-W-update',
                                        initializer=self.initializer,
                                        shape=[self.state_size, self.output_units],
                                        trainable=True)
        self.U_update = tf.get_variable(name=f'{self.name}-U-update',
                                        initializer=self.initializer,
                                        shape=[self.input_units, self.output_units],
                                        trainable=True)
        self.b_update = tf.get_variable(name=f'{self.name}-b-update',
                                        initializer=self.initializer,
                                        shape=[1, self.output_units],
                                        trainable=True)

        self.W_reset = tf.get_variable(name=f'{self.name}-W-reset',
                                       initializer=self.initializer,
                                       shape=[self.state_size, self.output_units],
                                       trainable=True)
        self.U_reset = tf.get_variable(name=f'{self.name}-U-reset',
                                       initializer=self.initializer,
                                       shape=[self.input_units, self.output_units],
                                       trainable=True)
        self.b_reset = tf.get_variable(name=f'{self.name}-b-reset',
                                       initializer=self.initializer,
                                       shape=[1, self.output_units],
                                       trainable=True)

        self.W = tf.get_variable(name=f'{self.name}-W',
                                 initializer=self.initializer,
                                 shape=[self.state_size, self.output_units],
                                 trainable=True)
        self.U = tf.get_variable(name=f'{self.name}-U',
                                 initializer=self.initializer,
                                 shape=[self.input_units, self.output_units],
                                 trainable=True)
        self.b = tf.get_variable(name=f'{self.name}-b',
                                 initializer=self.initializer,
                                 shape=[1, self.output_units],
                                 trainable=True)

        if self.use_skip_connections:
            self.R = tf.get_variable(name=f'{self.name}-R',
                                     initializer=self.initializer,
                                     shape=[2 * self.state_size, self.state_size],
                                     trainable=True)
            self.b_skip = tf.get_variable(name=f'{self.name}-b-skip',
                                          initializer=self.initializer,
                                          shape=[1, self.state_size],
                                          trainable=True)

    def __call__(self, inputs: tf.Tensor,
                 state: tf.Tensor,
                 skip_input: Optional[tf.Tensor] = None) -> Tuple[tf.Tensor, tf.Tensor, List[tf.Tensor]]:
        assert not self.use_skip_connections or skip_input is not None, 'Must provide a skip input when using skip connections'

        if self.use_skip_connections:
            concat_state = tf.concat([state, skip_input], axis=-1)  # B x 2D
            skip_gate = tf.math.sigmoid(tf.matmul(concat_state, self.R) + self.b_skip)
            state = skip_gate * state + (1.0 - skip_gate) * skip_input

        update_vector = tf.matmul(state, self.W_update) + tf.matmul(inputs, self.U_update) + self.b_update
        reset_vector = tf.matmul(state, self.W_reset) + tf.matmul(inputs, self.U_reset) + self.b_reset

        update_gate = tf.math.sigmoid(update_vector)
        reset_gate = tf.math.sigmoid(reset_vector)

        update_with_dropout = tf.nn.dropout(update_gate, keep_prob=self.dropout_keep_prob)
        reset_with_dropout = tf.nn.dropout(reset_gate, keep_prob=self.dropout_keep_prob)

        candidate_vector = tf.matmul(state * reset_with_dropout, self.W) + tf.matmul(inputs, self.U) + self.b
        candidate_state = self.activation(candidate_vector)
        next_state = update_with_dropout * state + (1.0 - update_with_dropout) * candidate_state

        return next_state, next_state, [update_gate, reset_gate]


class VanillaCell(RNNCell):

    def init_weights(self):
        self.W = tf.get_variable(name=f'{self.name}-W',
                                 initializer=self.initializer,
                                 shape=[self.state_size, self.output_units],
                                 trainable=True)
        self.U = tf.get_variable(name=f'{self.name}-U',
                                 initializer=self.initializer,
                                 shape=[self.input_units, self.output_units],
                                 trainable=True)
        self.b = tf.get_variable(name=f'{self.name}-b',
                                 initializer=self.initializer,
                                 shape=[1, self.output_units],
                                 trainable=True)

        if self.use_skip_connections:
            self.R = tf.get_variable(name=f'{self.name}-R',
                                     initializer=self.initializer,
                                     shape=[2 * self.state_size, self.state_size],
                                     trainable=True)
            self.b_skip = tf.get_variable(name=f'{self.name}-b-skip',
                                          initializer=self.initializer,
                                          shape=[1, self.state_size],
                                          trainable=True)

    def __call__(self, inputs: tf.Tensor,
                 state: tf.Tensor,
                 skip_input: Optional[tf.Tensor] = None) -> Tuple[tf.Tensor, tf.Tensor, List[tf.Tensor]]:
        assert not self.use_skip_connections or skip_input is None, 'Must provide a skip input when using skip connections'

        if self.use_skip_connections:
            concat_state = tf.concat([state, skip_input], axis=-1)
            skip_gate = tf.math.sigmoid(tf.matmul(concat_state, self.R) + self.b_skip)
            state = skip_gate * state + (1.0 - skip_gate) * skip_input

        candidate_vector = tf.matmul(state, self.W) + tf.matmul(inputs, self.U) + self.b
        candidate_vector_with_dropout = tf.nn.dropout(candidate_vector, keep_prob=self.dropout_keep_prob)

        next_state = self.activation(candidate_vector_with_dropout)
        return next_state, next_state, [candidate_vector]


class LSTM(RNNCell):

    @property
    def num_state_elements(self):
        # Each state is a tuple of (c, h)
        return 2

    def init_weights(self):
        self.W_i = tf.get_variable(name=f'{self.name}-W-i',
                                   initializer=self.initializer,
                                   shape=[self.state_size, self.output_units],
                                   trainable=True)
        self.U_i = tf.get_variable(name=f'{self.name}-U-i',
                                   initializer=self.initializer,
                                   shape=[self.input_units, self.output_units],
                                   trainable=True)
        self.b_i = tf.get_variable(name=f'{self.name}-b-i',
                                   initializer=self.initializer,
                                   shape=[1, self.output_units],
                                   trainable=True)

        self.W_o = tf.get_variable(name=f'{self.name}-W-o',
                                   initializer=self.initializer,
                                   shape=[self.state_size, self.output_units],
                                   trainable=True)
        self.U_o = tf.get_variable(name=f'{self.name}-U-o',
                                   initializer=self.initializer,
                                   shape=[self.input_units, self.output_units],
                                   trainable=True)
        self.b_o = tf.get_variable(name=f'{self.name}-b-o',
                                   initializer=self.initializer,
                                   shape=[1, self.output_units],
                                   trainable=True)

        self.W_f = tf.get_variable(name=f'{self.name}-W-f',
                                   initializer=self.initializer,
                                   shape=[self.state_size, self.output_units],
                                   trainable=True)
        self.U_f = tf.get_variable(name=f'{self.name}-U-f',
                                   initializer=self.initializer,
                                   shape=[self.input_units, self.output_units],
                                   trainable=True)
        self.b_f = tf.get_variable(name=f'{self.name}-b-f',
                                   initializer=self.initializer,
                                   shape=[1, self.output_units],
                                   trainable=True)

        self.W = tf.get_variable(name=f'{self.name}-W',
                                 initializer=self.initializer,
                                 shape=[self.state_size, self.output_units],
                                 trainable=True)
        self.U = tf.get_variable(name=f'{self.name}-U',
                                 initializer=self.initializer,
                                 shape=[self.input_units, self.output_units],
                                 trainable=True)
        self.b = tf.get_variable(name=f'{self.name}-b',
                                 initializer=self.initializer,
                                 shape=[1, self.output_units],
                                 trainable=True)

        if self.use_skip_connections:
            self.R_c = tf.get_variable(name=f'{self.name}-R-c',
                                       initializer=self.initializer,
                                       shape=[2 * self.state_size, self.state_size],
                                       trainable=True)
            self.R_h = tf.get_variable(name=f'{self.name}-R-h',
                                       initializer=self.initializer,
                                       shape=[2 * self.state_size, self.state_size],
                                       trainable=True)
            self.b_skip_c = tf.get_variable(name=f'{self.name}-b-skip-c',
                                            initializer=self.initializer,
                                            shape=[1, self.state_size],
                                            trainable=True)
            self.b_skip_h = tf.get_variable(name=f'{self.name}-b-skip-h',
                                            initializer=self.initializer,
                                            shape=[1, self.state_size],
                                            trainable=True)

    def zero_state(self, batch_size: tf.Tensor, dtype: Any) -> tf.Tensor:
        # The state is double the size to store values for the tuple (c, h)
        state = tf.fill(dims=[batch_size, 2 * self.state_size], value=0, name=f'{self.name}-initial-state')
        return tf.cast(state, dtype=dtype)

    def __call__(self, inputs: tf.Tensor,
                 state: tf.Tensor,
                 skip_input: Optional[tf.Tensor] = None) -> Tuple[tf.Tensor, tf.Tensor, List[tf.Tensor]]:
        assert not self.use_skip_connections or skip_input is not None, 'Must provide a skip input when using skip connections'

        state_c, state_h = state[:, 0:self.state_size], state[:, self.state_size:]

        if self.use_skip_connections:
            skip_input_c, skip_input_h = state[:, 0:self.state_size], state[:, self.state_size:]

            concat_state_c = tf.concat([state_c, skip_input_c], axis=-1)
            state_c_gate = tf.math.sigmoid(tf.matmul(concat_state_c, self.R_c) + self.b_skip_c)
            state_c = state_c_gate * state_c + (1.0 - state_c_gate) * skip_input_c

            concat_state_h = tf.concat([state_h, skip_input_h], axis=-1)
            state_h_gate = tf.math.sigmoid(tf.matmul(concat_state_h, self.R_h) + self.b_skip_h)
            state_h = state_h_gate * state_h + (1.0 - state_h_gate) * skip_input_h

        write_vector = tf.matmul(state_h, self.W_i) + tf.matmul(inputs, self.U_i) + self.b_i
        read_vector = tf.matmul(state_h, self.W_o) + tf.matmul(inputs, self.U_o) + self.b_o
        forget_vector = tf.matmul(state_h, self.W_f) + tf.matmul(inputs, self.U_f) + self.b_f

        write_gate = tf.math.sigmoid(write_vector)
        read_gate = tf.math.sigmoid(read_vector)
        forget_gate = tf.math.sigmoid(forget_vector)

        write_with_dropout = tf.nn.dropout(write_gate, keep_prob=self.dropout_keep_prob)
        read_with_dropout = tf.nn.dropout(read_gate, keep_prob=self.dropout_keep_prob)
        forget_with_dropout = tf.nn.dropout(forget_gate, keep_prob=self.dropout_keep_prob)

        candidate_vector = tf.matmul(state_h, self.W) + tf.matmul(inputs, self.U) + self.b
        candidate_state = self.activation(candidate_vector)

        next_c = forget_with_dropout * state_c + write_with_dropout * candidate_state
        next_h = read_with_dropout * tf.nn.tanh(next_c)

        next_state = tf.concat([next_c, next_h], axis=-1)

        return next_h, next_state, [write_gate, read_gate, forget_gate]