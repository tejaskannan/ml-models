import os
import numpy as np
from argparse import ArgumentParser
from collections import namedtuple
from dpu_utils.utils import RichPath
from typing import Dict, Union, List, Optional

from models.rnn_model import RNNModel
from dataset.dataset import DataSeries
from dataset.rnn_sample_dataset import RNNSampleDataset
from utils.hyperparameters import HyperParameters
from utils.file_utils import extract_model_name
from utils.rnn_utils import get_logits_name
from utils.constants import HYPERS_PATH, TEST_LOG_PATH, TRAIN, VALID, TEST, METADATA_PATH, SMALL_NUMBER, BIG_NUMBER, OPTIMIZED_TEST_LOG_PATH
from utils.misc import sigmoid
from utils.np_utils import thresholded_predictions, f1_score, precision, recall
from utils.testing_utils import ClassificationMetric
from utils.rnn_utils import get_prediction_name
from post_processing.threshold_optimizer import ThresholdOptimizer


EvaluationResult = namedtuple('EvaluationResult', ['accuracy', 'precision', 'recall', 'f1_score', 'level', 'thresholds', 'latency'])


def print_eval_result(result: EvaluationResult):
    print(f'Results for thresholds: {result.thresholds}')
    print(f'Precision: {result.precision:.4f}')
    print(f'Recall: {result.recall:.4f}')
    print(f'F1 Score: {result.f1_score:.4f}')
    print(f'Accuracy: {result.accuracy:.4f}')
    print(f'Average Computed Levels: {result.level:.4f}')
    print(f'Average Latency: {result.latency:.4f}')


def result_to_dict(result: EvaluationResult):
    return {key.upper(): value for key, value in result._asdict().items()}


def get_dataset(model_name: str, save_folder: RichPath, dataset_folder: Optional[str]) -> RNNSampleDataset:
    metadata_file = save_folder.join(METADATA_PATH.format(model_name))
    metadata = metadata_file.read_by_file_suffix()

    if dataset_folder is None:
        train_folder = metadata['data_folders'][TRAIN.upper()].path
        valid_folder = metadata['data_folders'][VALID.upper()].path
        test_folder = metadata['data_folders'][TEST.upper()].path
    else:
        assert os.path.exists(dataset_folder), f'The dataset folder {dataset_folder} does not exist!'
        train_folder = os.path.join(dataset_folder, TRAIN)
        valid_folder = os.path.join(dataset_folder, VALID)
        test_folder = os.path.join(dataset_folder, TEST)

    return RNNSampleDataset(train_folder, valid_folder, test_folder)


def get_model(model_name: str, hypers: HyperParameters, save_folder: RichPath) -> RNNModel:
    model = RNNModel(hypers, save_folder)
    model.restore(name=model_name, is_train=False)
    return model


def evaluate_thresholds(model: RNNModel,
                        thresholds: List[float],
                        dataset: RNNSampleDataset,
                        series: DataSeries,
                        test_log: Dict[str, Dict[str, float]]) -> EvaluationResult:
    test_dataset = dataset.minibatch_generator(series,
                                               metadata=model.metadata,
                                               batch_size=model.hypers.batch_size,
                                               should_shuffle=False,
                                               drop_incomplete_batches=True)

    logit_ops = [get_logits_name(i) for i in range(model.num_outputs)]

    predictions_list: List[np.ndarray] = []
    labels_list: List[np.ndarray] = []
    levels_list: List[np.ndarray] = []
    latencies: List[float] = []

    for batch_num, batch in enumerate(test_dataset):
        feed_dict = model.batch_to_feed_dict(batch, is_train=False)
        logits = model.execute(feed_dict, logit_ops)

        # Concatenate logits into a 2D array (logit_ops is already ordered by level)
        logits_concat = np.squeeze(np.concatenate([logits[op] for op in logit_ops], axis=-1))
        probabilities = sigmoid(logits_concat)
        labels = np.vstack(batch['output'])

        output = thresholded_predictions(probabilities, thresholds)
        predictions = output.predictions
        computed_levels = output.indices

        predictions_list.append(predictions)
        labels_list.append(labels)
        levels_list.append(computed_levels + 1.0)

        for level in computed_levels:
            level_name = get_prediction_name(level)
            latencies.append(test_log[level_name][ClassificationMetric.LATENCY.name])

        print(f'Completed batch {batch_num + 1}', end='\r')
    print()

    predictions = np.expand_dims(np.concatenate(predictions_list, axis=0), axis=-1)
    labels = np.vstack(labels_list)

    avg_levels = np.average(np.vstack(levels_list))
    p = precision(predictions, labels)
    r = recall(predictions, labels)
    f1 = f1_score(predictions, labels)
    accuracy = np.average(1.0 - np.abs(predictions - labels))
    avg_latency = np.average(latencies)

    return EvaluationResult(precision=p,
                            recall=r,
                            f1_score=f1,
                            accuracy=accuracy,
                            level=avg_levels,
                            latency=avg_latency,
                            thresholds=list(thresholds))


def optimize_thresholds(optimizer_params: Dict[str, Union[float, int]], path: str, dataset_folder: Optional[str]):
    save_folder, model_file = os.path.split(path)

    model_name = extract_model_name(model_file)
    assert model_name is not None, f'Could not extract name from file: {model_file}'

    save_folder = RichPath.create(save_folder)

    # Extract hyperparameters
    hypers_name = HYPERS_PATH.format(model_name)
    hypers = HyperParameters.create_from_file(save_folder.join(hypers_name))

    dataset = get_dataset(model_name, save_folder, dataset_folder)
    model = get_model(model_name, hypers, save_folder)

    # Get test log
    test_log_path = save_folder.join(TEST_LOG_PATH.format(model_name))
    assert test_log_path.exists(), f'Must perform model testing before post processing'
    test_log = list(test_log_path.read_by_file_suffix())[0]

    print('Starting optimization')

    opt_outputs: List[OptimizerOutput] = []
    for _ in range(optimizer_params['instances']):
        optimizer = ThresholdOptimizer(population_size=optimizer_params['population_size'],
                                       mutation_rate=optimizer_params['mutation_rate'],
                                       batch_size=optimizer_params['batch_size'],
                                       selection_count=optimizer_params['selection_count'],
                                       iterations=optimizer_params['iterations'])
        output = optimizer.optimize(model, dataset)
        
        opt_outputs.append(output)
        print('==========')

    print('Completed optimization. Choosing the best model...')
    best_thresholds = None
    best_f1_score = -BIG_NUMBER
    for opt_output in opt_outputs:
        result = evaluate_thresholds(model=model,
                                     thresholds=opt_output.thresholds,
                                     dataset=dataset,
                                     series=DataSeries.VALID,
                                     test_log=test_log)

        if result.f1_score > best_f1_score:
            best_thresholds = result.thresholds
            best_f1_score = result.f1_score

    print('Completed selection. Starting evaluation....')

    baseline = [0.5 for _ in output.thresholds]
    result = evaluate_thresholds(model=model,
                                 thresholds=baseline,
                                 dataset=dataset,
                                 series=DataSeries.TEST,
                                 test_log=test_log)
    print_eval_result(result)

    print('===============')

    result = evaluate_thresholds(model=model,
                                 thresholds=best_thresholds,
                                 dataset=dataset,
                                 series=DataSeries.TEST,
                                 test_log=test_log)
    print_eval_result(result)

    # Save new results
    test_log['scheduled_genetic'] = result_to_dict(result)
    optimized_test_log_path = save_folder.join(OPTIMIZED_TEST_LOG_PATH.format(model_name))
    optimized_test_log_path.save_as_compressed_file([test_log])


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('--model-path', type=str, required=True)
    parser.add_argument('--optimizer-params', type=str, required=True)
    parser.add_argument('--dataset-folder', type=str)
    args = parser.parse_args()

    optimizer_params_file = RichPath.create(args.optimizer_params)
    assert optimizer_params_file.exists(), f'The file {optimizer_params_file} does not exist'

    optimizer_params = optimizer_params_file.read_by_file_suffix()

    optimize_thresholds(optimizer_params, args.model_path, args.dataset_folder)