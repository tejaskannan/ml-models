from .threshold_optimizer import ThresholdOptimizer
from .genetic_optimizer import GeneticOptimizer
from .sim_anneal_optimizer import SimAnnealOptimizer
from .gradient_optimizer import GradientOptimizer
from .multiclass_genetic_optimizer import MulticlassGeneticOptimizer


def get_optimizer(name: str, iterations: int, batch_size: int, level_weight: float, mode: str, **kwargs) -> ThresholdOptimizer:
    name = name.lower()

    if name == 'genetic':
        return GeneticOptimizer(population_size=kwargs['population_size'],
                                mutation_rate=kwargs['mutation_rate'],
                                batch_size=batch_size,
                                mode=mode,
                                level_weight=level_weight,
                                crossover_rate=kwargs['crossover_rate'],
                                crossover_type=kwargs['crossover_type'],
                                mutation_type=kwargs['mutation_type'],
                                steady_state_count=kwargs['steady_state_count'],
                                should_sort=kwargs['should_sort'],
                                iterations=iterations)
    if name in ('multiclass', 'multiclass_genetic'):
        return MulticlassGeneticOptimizer(population_size=kwargs['population_size'],
                                          mutation_rate=kwargs['mutation_rate'],
                                          batch_size=batch_size,
                                          mode=mode,
                                          level_weight=level_weight,
                                          crossover_rate=kwargs['crossover_rate'],
                                          crossover_type=kwargs['crossover_type'],
                                          mutation_type=kwargs['mutation_type'],
                                          steady_state_count=kwargs['steady_state_count'],
                                          should_sort=kwargs['should_sort'],
                                          iterations=iterations,
                                          num_classes=kwargs['num_classes'])
    elif name in ('simulated_anneal', 'simulated-anneal', 'sim_anneal', 'sim-anneal'):
        return SimAnnealOptimizer(instances=kwargs['instances'],
                                  epsilon=kwargs['epsilon'],
                                  anneal=kwargs['anneal'],
                                  num_candidates=kwargs['num_candidates'],
                                  move_norm=kwargs['move_norm'],
                                  batch_size=batch_size,
                                  iterations=iterations,
                                  level_weight=level_weight,
                                  mode=mode)
    elif name in ('gradient', 'gradient_optimizer', 'gradient-optimizer'):
        return GradientOptimizer(iterations=iterations,
                                 batch_size=batch_size,
                                 mode=mode,
                                 update_type=kwargs['update_type'],
                                 update_params=kwargs['update_params'],
                                 sharpen_factor=kwargs['sharpen_factor'],
                                 level_weight=level_weight,
                                 tolerance=kwargs['tolerance'])
    else:
        raise ValueError(f'Unknown optimizer: {name}')
