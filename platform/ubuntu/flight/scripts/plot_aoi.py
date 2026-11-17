"""Aggregate held-out sensor ages equally across slots, traces and training seeds."""
import argparse
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    folder = parser.parse_args().directory
    files=sorted(folder.glob('*/evaluation.csv')) or [folder/'evaluation.csv']
    raw=pd.concat([pd.read_csv(file) for file in files],ignore_index=True)
    per_seed = raw.groupby(['algorithm','steps','train_seed']).mean_aoi_s.mean()
    table = per_seed.groupby(['algorithm','steps']).agg(['mean','std','count'])
    table.to_csv(folder/'summary.csv')
    fig, axis = plt.subplots(figsize=(7,4))
    for algo, rows in table.groupby(level=0):
        x = rows.index.get_level_values('steps')
        axis.plot(x,rows['mean'],marker='o',label=algo)
        axis.fill_between(x,rows['mean']-rows['std'],rows['mean']+rows['std'],alpha=.12)
    axis.set(xlabel='Training environment steps',ylabel='Held-out mean sensor AoI (s)',xticks=sorted(raw.steps.unique()))
    axis.legend(ncol=2)
    fig.tight_layout()
    fig.savefig(folder/'aoi.png',dpi=165)
    print(table.xs(raw.steps.max(),level='steps'))
