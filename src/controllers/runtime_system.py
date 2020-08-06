import numpy as np
import os.path
from collections import namedtuple
from enum import Enum, auto
from typing import List, Dict, Any

from controllers.controller_utils import clip, get_budget_index, ModelResults
from controllers.model_controllers import Controller, FixedController, RandomController, BudgetWrapper, CONTROLLER_PATH, levels_to_execute, predictions_for_levels
from controllers.model_controllers import SkipRNNController
from controllers.runtime_controllers import PIDController, BudgetController, BudgetDistribution
from dataset.dataset import DataSeries, Dataset
from models.adaptive_model import AdaptiveModel
from models.tf_model import TFModel
from utils.rnn_utils import get_logits_name, get_stop_output_name
from utils.file_utils import extract_model_name
from utils.constants import INPUTS, OUTPUT, SEQ_LENGTH, NUM_CLASSES, SEQ_LENGTH, SMALL_NUMBER


# Constants
KP = 1.0
KD = 1.0 / 32.0
KI = 1.0 / 64.0
WINDOW_SIZE = 20
INTEGRAL_BOUNDS = (-16, 16)
INTEGRAL_WINDOW = 100
PANIC_FRAC = 0.95


# Defines the type of runtime system
class SystemType(Enum):
    ADAPTIVE = auto()
    RANDOMIZED = auto()
    GREEDY = auto()
    FIXED = auto()
    SKIP_RNN = auto()


def estimate_label_counts(predictions: np.ndarray, stop_probs: np.ndarray, thresholds: np.ndarray, num_classes: int) -> Dict[int, np.ndarray]:
    """
    Counts the number of levels for each class predicted by the adaptive model. This count is based on the validation set
    and used as an estimate for the behavior on the testing set.

    Args:
        predictions: A [N, L] array of predictions for each sample (N) and level (L) of the model
        stop_probs: A [N, L] array of stop probabilities for each sample (N) and level (L) of the model
        thresholds: A [L] array of thresholds for the current budget. These are derived from the controller.
        num_classes: The number of classes.
    Returns:
        A dictionary mapping the class index to an [L] array of inference counts per level
    """
    # Compute the number of levels to execute using the thresholds and stop probabilities
    levels = levels_to_execute(np.expand_dims(stop_probs, axis=0), np.expand_dims(thresholds, axis=0))  # [1, N]

    batch_idx = np.arange(start=0, stop=predictions.shape[0])
    pred = predictions_for_levels(predictions, levels, batch_idx)  # [1, N]
    
    # Reshape arrays to size [N]
    pred = pred[0]
    levels = levels[0]

    # TODO: This operation is expensive because it executes the model on the validation set. Instead, we should give the validation stop outputs
    # and use the controller on the already_computed results. This requires executing the adaptive models on the validation set AND the testing set
    # before starting any simulations.
    # levels, predictions = controller.predict_levels(series=DataSeries.VALID, budget=budget)
    # batch_size = predictions.shape[0]

    result: Dict[int, np.ndarray] = dict()

    # Collect the counts for each class
    num_levels = predictions.shape[1]
    for class_idx in range(num_classes):
        class_mask = (pred == class_idx).astype(int)

        class_level_counts = np.bincount(levels, minlength=num_levels, weights=class_mask)
        result[class_idx] = class_level_counts

    return result


class RuntimeSystem:

    def __init__(self,
                 valid_results: ModelResults,
                 test_results: ModelResults,
                 system_type: SystemType,
                 model_path: str,
                 dataset_folder: str,
                 num_classes: int,
                 num_levels: int,
                 seq_length: int):
        self._system_type = system_type
        self._test_results = test_results
        self._valid_results = valid_results

        self._model_path = model_path
        self._dataset_folder = dataset_folder

        self._num_classes = num_classes
        self._num_levels = num_levels
        self._seq_length = seq_length

        save_folder, model_file_name = os.path.split(model_path)
        model_name = extract_model_name(model_file_name)
        
        self._model_name = model_name
        self._save_folder = save_folder
            
        # Results from the testing set. These are precomputed for efficiency.
        self._level_predictions = test_results.predictions
        self._stop_probs = test_results.stop_probs
        self._labels = test_results.labels

        # Results from the validation set. These are used by a few controllers
        # to select the model or model level
        self._valid_accuracy = valid_results.accuracy  # [L]
        self._valid_predictions = valid_results.predictions  # [N, L]
        self._valid_stop_probs = valid_results.stop_probs  # [N, L]

        self._budget_controller = None

        self._name = '{0} {1}'.format(model_name.split('-')[0], system_type.name)
        self._seed = hash(self._name) % 1000

        # For some systems, we can load the controller now. Otherwise, we wait until later
        if self._system_type == SystemType.ADAPTIVE:
            self._controller = Controller.load(os.path.join(save_folder, CONTROLLER_PATH.format(model_name)), dataset_folder=dataset_folder)
        elif self._system_type == SystemType.SKIP_RNN:
            self._controller = SkipRNNController(sample_counts=valid_results.stop_probs,
                                                 model_accuracy=valid_results.accuracy,
                                                 seq_length=seq_length)
        else:
            self._controller = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def save_folder(self) -> str:
        return self._save_folder

    @property
    def system_type(self) -> SystemType:
        return self._system_type

    def get_power(self) -> np.ndarray:
        return np.array(self._budget_controller.power)

    def get_energy(self) -> np.ndarray:
        return np.cumsum(self._budget_controller.power)

    def get_num_correct(self) -> np.ndarray:
        return np.cumsum(self._num_correct)

    def get_target_budgets(self) -> np.ndarray:
        return np.array(self._target_budgets).reshape(-1)

    def init_for_budget(self, budget: float, max_time: int):
        # Make controller based on the model type
        if self._system_type == SystemType.RANDOMIZED:
            self._controller = RandomController(budgets=[budget], seq_length=self._seq_length, num_levels=self._num_levels)
            self._controller.fit(series=None)
        elif self._system_type == SystemType.GREEDY:
            level = np.argmax(self._valid_accuracy)
            self._controller = FixedController(model_index=level)
        elif self._system_type == SystemType.FIXED:
            level = get_budget_index(budget=budget, level_accuracy=self._valid_accuracy)
            self._controller = FixedController(model_index=level)
        elif self._system_type == SystemType.ADAPTIVE:
            # Make the budget distribution and PID controller
            self._pid_controller = BudgetController(kp=KP,
                                                    ki=KI,
                                                    kd=KD,
                                                    integral_bounds=INTEGRAL_BOUNDS,
                                                    integral_window=INTEGRAL_WINDOW)
            # Create the power distribution
            thresholds = self._controller.get_thresholds(budget=budget)
            prior_counts = estimate_label_counts(predictions=self._valid_predictions,
                                                 stop_probs=self._valid_stop_probs,
                                                 thresholds=thresholds,
                                                 num_classes=self._num_classes)
            self._budget_distribution = BudgetDistribution(prior_counts=prior_counts,
                                                           budget=budget,
                                                           max_time=max_time,
                                                           num_levels=self._num_levels,
                                                           num_classes=self._num_classes,
                                                           seq_length=self._seq_length,
                                                           panic_frac=PANIC_FRAC)

        # Apply budget wrapper to the controller
        assert self._controller is not None, 'Must have a valid controller'
        self._budget_controller = BudgetWrapper(controller=self._controller,
                                                model_predictions=self._level_predictions,
                                                max_time=max_time,
                                                num_classes=self._num_classes,
                                                num_levels=self._num_levels,
                                                seq_length=self._seq_length,
                                                budget=budget,
                                                seed=self._seed)
        self._budget_step = 0
        self._num_correct = []
        self._target_budgets = []

    def step(self, budget: float, power_noise: float, time: int):
        stop_probs = self._stop_probs[time] if self._stop_probs is not None and time < len(self._stop_probs) else None

        budget += self._budget_step
        pred, level, power = self._budget_controller.predict_sample(stop_probs=stop_probs,
                                                                    budget=budget,
                                                                    noise=power_noise,
                                                                    current_time=time)
        label = self._labels[time]
       
        if pred is None:
            is_correct = 0
        else:
            is_correct = float(abs(label - pred) < SMALL_NUMBER)

        self._num_correct.append(is_correct)
        self._target_budgets.append(budget)

        # Update the budget distribution for the adaptive system
        if self._system_type == SystemType.ADAPTIVE and pred is not None:
            self._budget_distribution.update(label=pred, level=level, power=power)

        # Update the adaptive controller parameters
        if time % WINDOW_SIZE == 0 and self._system_type == SystemType.ADAPTIVE:
            current_budget = self._budget_distribution.get_budget(time + 1)
            power_so_far = np.average(self._budget_controller.power)
            self._budget_step = self._pid_controller.step(y_true=current_budget, y_pred=power_so_far, time=time)
