import re
import json
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.axes import Axes
from argparse import ArgumentParser
from collections import defaultdict, OrderedDict
from dpu_utils.utils import RichPath
from os.path import split, join, exists
from os import mkdir
from typing import Dict, Tuple, List, Optional, DefaultDict, Any
from pandas.plotting import register_matplotlib_converters

from rnn_sample_model import RNNSampleModel
from rnn_sample_dataset import RNNSampleDataset
from testing_utils import TestMetrics, geometric_standard_deviation
from utils.hyperparameters import HyperParameters, extract_hyperparameters


register_matplotlib_converters()


STAT_LABEL_DICT = {
    'mean': ('Mean', 'M'),
    'median': ('Median', 'Med'),
    'geom_mean': ('Geometric Mean', 'GeoM'),
}


def extract_model_name(model_file: str) -> str:
    match = re.match(r'^model-([^\.]+)\.ckpt.*$', model_file)
    if not match:
        if model_file.startswith('model-'):
            return model_file[len('model-'):]
        return model_file
    return match.group(1)


def evaluate_model(model_params: Dict[str, str], dataset: RNNSampleDataset,
                   batch_size: Optional[int], num_batches: Optional[int]) -> TestMetrics:
    hypers = extract_hyperparameters(model_params['params_file'])[0]

    path_tokens = split(model_params['model_path'])
    folder, file_name = path_tokens[0], path_tokens[1]
    model = RNNSampleModel(hypers, folder)

    model_name = extract_model_name(file_name)

    model.restore_parameters(model_name)
    model.make(is_train=False)
    model.restore_weights(model_name)

    name = join(folder, f'model-{model_name}')
    metrics = model.predict(dataset, name, batch_size, num_batches)
    return metrics


def get_stat(metrics: List[TestMetrics], stat_name: str) -> float:
    values: List[float] = []

    for metric in metrics:
        if stat_name == 'median':
            values.append(metric.median)
        elif stat_name == 'geom_mean':
            values.append(metric.geom_mean)
        elif stat_name == 'mean':
            values.append(metric.mean)
        else:
            raise ValueError('Unknown stat name: {stat_name}')

    if stat_name == 'median':
        return np.median(values)  # This is not technically correct but is good enough for now
    elif stat_name == 'geom_mean':
        return np.prod(values)**(1.0 / len(metrics))
    elif stat_name == 'mean':
        return np.average(values)
    raise ValueError('Unknown stat name: {stat_name}')


def plot_axis(test_metrics: Dict[str, List[TestMetrics]],
              series: str,
              stat_name: str,
              x_values: List[float],
              title: str,
              xlabel: str,
              ylabel: str,
              ax: Axes):
    # Sample Fraction vs Squared Error
    for label, metrics in test_metrics.items():
        y = [get_stat([m[series][op] for m in metrics], stat_name) for op in prediction_ops]
        ax.errorbar(x=x_values, y=y, fmt='-o', label=label, linewidth=2, markersize=5)

    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.set_xticks(ticks=x_values)


def plot_predictions(metrics: TestMetrics,
                     series: str,
                     output_name: str,
                     output_folder: Optional[str],
                     sample_frac: float):
    with plt.style.context('ggplot'):

        fig, ax = plt.subplots(1, 1, figsize=(12, 5))

        predictions = next(iter(metrics.predictions.values()))
        dates = [pred.sample_id for pred in predictions]
        expected: List[float] = [pred.expected for pred in predictions]

        ax.plot(dates, expected, label='Actual')

        for prediction_op, preds in metrics.predictions.items():
            dates = [pred.sample_id for pred in preds]
            predictions = [pred.prediction for pred in preds]

            fraction = (int(prediction_op[-1]) + 1) * sample_frac
            label = f'Fraction {fraction:.2f}'
            ax.plot(dates, predictions, label=label)

        ax.set_title(f'{series} Model Predictions on Test Set')
        ax.set_xlabel('Date')
        ax.set_ylabel(output_name)
        ax.legend()

        plt.gcf().autofmt_xdate()

        if output_folder is None:
            plt.show()
        else:
            plt.savefig(join(output_folder, f'{series}_predicted_values.pdf'))


def plot_latency(test_metrics: Dict[str, List[TestMetrics]],
                 prediction_ops: List[str],
                 sample_frac: float,
                 output_folder: Optional[str],
                 dataset_name: str,
                 stat_name: str):
    sample_fractions = [(i+1) * sample_frac for i in range(len(prediction_ops))]

    # Create axis
    fig, ax = plt.subplots(1, 1, figsize=(7, 4))

    stat_name = stat_name.lower()
    for series, metrics_list in test_metrics.items():
        metrics = metrics_list[0]

        if stat_name == 'median':
            y = [metrics.latency[op].median for op in prediction_ops]
            yerr = [[metrics.latency[op].first_quartile, metrics.latency[op].third_quartile] for op in prediction_ops]
        elif stat_name == 'mean':
            y = [metrics.latency[op].mean for op in prediction_ops]
            yerr = [metrics.latency[op].std for op in prediction_ops]
        elif stat_name == 'geom_mean':
            y = [metrics.latency[op].geom_mean for op in prediction_ops]
            yerr = [metrics.latency[op].geom_std for op in prediction_ops]
        else:
            raise ValueError(f'Unknown stat name {stat_name}.')

        ax.errorbar(x=sample_fractions, y=y, fmt='-o', capsize=2, label=series, linewidth=2, markersize=5)

    stat_label, acronym = STAT_LABEL_DICT[stat_name]

    ax.set_xlabel('Input Fraction')
    ax.set_ylabel('Latency (ms)')
    ax.set_title(f'{stat_label} Inference Latency on the {dataset_name} Dataset')
    ax.set_xticks(ticks=sample_fractions)
    ax.legend()

    plt.tight_layout()

    if output_folder is None:
        plt.show()
        return

    output_folder_path = RichPath.create(output_folder)
    output_folder_name = split(output_folder)[1] + '-' + stat_name
    plot_file = output_folder_path.join(output_folder_name + '-latency.pdf')
    plt.savefig(plot_file.path)


def plot_errors(test_metrics: Dict[str, List[TestMetrics]],
                prediction_ops: List[str],
                sample_frac: float,
                output_folder: Optional[str],
                dataset_name: str,
                stat_name: str,
                test_params: Dict[str, Any]):
    if len(test_metrics) <= 0:
        raise ValueError('Must provde some metrics to graph.')

    if stat_name not in ('mean', 'median', 'geom_mean'):
        raise ValueError(f'Unknown aggregate metric {stat_name}.')

    plt.style.use('ggplot')

    sample_fractions = [(i+1) * sample_frac for i in range(len(prediction_ops))]

    # Create axes
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 5))

    stat_label, acronym = STAT_LABEL_DICT[stat_name]

    # Plot each metric
    plot_axis(test_metrics=test_metrics,
              series='squared_error',
              stat_name=stat_name,
              x_values=sample_fractions,
              title=f'{stat_label} Squared Error ({acronym}SE) on the {dataset_name} Dataset',
              xlabel='Input Fraction',
              ylabel=f'{acronym}SE',
              ax=ax1)

    plot_axis(test_metrics=test_metrics,
              series='abs_percentage_error',
              stat_name=stat_name,
              x_values=sample_fractions,
              title=f'{stat_label} Absolute Percentage Error ({acronym}APE) on the {dataset_name} Dataset',
              xlabel='Input Fraction',
              ylabel=f'{acronym}APE',
              ax=ax2)

    handles, labels = ax2.get_legend_handles_labels()
    fig.legend(handles, labels)

    plt.tight_layout()

    if output_folder is None:
        plt.show()
        return

    output_folder_path = RichPath.create(output_folder)
    output_folder_path.make_as_dir()

    output_folder_name = split(output_folder)[1] + '-' + stat_name
    plot_file = output_folder_path.join(output_folder_name + '.pdf')
    params_file = output_folder_path.join(output_folder_name + '_params.jsonl.gz')

    plt.savefig(plot_file.path)
    params_file.save_as_compressed_file([test_params])

    # For now, we save the metrics as a pickle file because Numpy Arrays
    # are not JSON serializable. This should be changed to compressed
    # JSONL files for readability.
    metrics_file = RichPath.create(output_folder).join('metrics.pkl.gz')
    metrics_file.save_as_compressed_file(test_metrics)


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('--test-params-file', type=str, required=True)
    args = parser.parse_args()

    assert exists(args.test_params_file), f'The file {args.test_params_file} does not exist!'
    with open(args.test_params_file, 'r') as test_params_file:
        test_params = json.load(test_params_file)

    metrics: DefaultDict[str, TestMetrics] = defaultdict(list)
    num_models = len(test_params['models'])

    model_folder = RichPath.create(test_params['model_folder'])

    for i, model_params in enumerate(test_params['models']):
        model_test_log = model_folder.join(model_params['test_log_path'])
        metrics[model_params['model_name']].append(model_test_log.read_by_file_suffix())

    sample_frac = test_params['sample_frac']
    num_outputs = int(1.0 / sample_frac)
    prediction_ops = [f'prediction_{i}' for i in range(num_outputs)]

    output_folder = test_params.get('output_folder')
    for stat_name in ['mean', 'geom_mean']:
        plot_errors(test_metrics=metrics,
                    sample_frac=sample_frac,
                    prediction_ops=prediction_ops,
                    output_folder=output_folder,
                    dataset_name=test_params['dataset_name'],
                    stat_name=stat_name,
                    test_params=test_params)
        plot_latency(test_metrics=metrics,
                     sample_frac=sample_frac,
                     prediction_ops=prediction_ops,
                     output_folder=output_folder,
                     dataset_name=test_params['dataset_name'],
                     stat_name=stat_name)