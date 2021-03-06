# coding: gbk
"""
@author: sdy
@email: sdy@epri.sgcc.com.cn
"""

import numpy as np
import pandas as pd
import random
import os
import shutil

from core.power import Power
from core.topo import PowerGraph
from common.cmd_util import call_wmlf, check_lfcal

EPS = 1e-8


def set_values(data, etype, column, values, delta=False):
    """ 设置指定设备类型指定列的数据。

    :param data: dict of pd.DataFrame. 数据集合。
    :param etype: str. 设备类型。
    :param column: str. 列名。
    :param values: dict. 用于修改指定设备；
                   or np.array 全部修改的数值；
                   or pd.Series 全部或部分修改的数值。
    :param delta: bool. False表示values为指定值；True表示values为变化量。
    """
    if etype not in data or column not in data[etype]:
        raise ValueError('Data incomplete! [%s, %s]' % (etype, column))
    if isinstance(values, dict):
        for k in values:
            if delta:
                data[etype].loc[k, column] += values[k]
            else:
                data[etype].loc[k, column] = values[k]
    else:
        if delta:
            data[etype][column] += values
        else:
            data[etype][column] = values


def distribute_generators_p(generators, delta, indices=None, sigma=None, clip=True):
    """ 投运机组按比例承担总有功变化量，修改机组表的p0列。

    :param generators: pd.DataFrame. 机组数据表。
    :param delta: float. 总有功变化量（p.u.）。
    :param indices: list. 指定参与机组的索引列表；
                    or None. 全部投运机组参与。
    :param sigma: float. 随机变化率的方差，变化率~N(1.0, sigma)。
                    or None. 不随机变化。
    :param clip: bool. 是否保持有功在限值之内。
    :return: float. 分配后的剩余功率，若为0.0代表全部分配完毕。
    """
    sub = generators[generators['mark'] == 1]
    if indices:
        indices = [i for i in indices if i in sub.index]
        sub = sub.loc[indices]
    if delta > 0.:
        sub = sub[sub['pmax'] > sub['p0']]
        margin = sub['pmax'] - sub['p0']
        margin_sum = np.sum(margin)
        if margin_sum <= delta:  # not enough
            generators.loc[sub.index, 'p0'] = sub['pmax']
            return delta - margin_sum
    else:
        sub = sub[sub['p0'] > sub['pmin']]
        margin = sub['p0'] - sub['pmin']
        margin_sum = np.sum(margin)
        if margin_sum <= -delta:  # not enough
            generators.loc[sub.index, 'p0'] = sub['pmin']
            return delta + margin
    if sigma:
        margin *= np.random.normal(loc=1.0, scale=sigma, size=(len(margin),))
        margin_sum = np.sum(margin)
    generators.loc[sub.index, 'p0'] = sub['p0'] + margin / margin_sum * delta
    if clip:
        generators['p0'] = np.clip(generators['p0'],
                                   generators['pmin'], generators['pmax'])

    return 0.


def distribute_loads_p(loads, delta, indices=None, p_sigma=None,
                       keep_factor=False, factor_sigma=None, clip=True):
    """ 负荷按比例承担总有功变化量，修改负荷表的p0和q0列。

    :param loads: pd.DataFrame. 负荷数据表。
    :param delta: float. 总有功变化量（p.u.）。
    :param indices: list. 指定参与负荷的索引列表；
                    or None. 全部投运负荷参与。
    :param p_sigma: float. 随机有功变化率的方差，变化率~N(1.0, p_sigma)。
                    or None. 不随机变化。
    :param keep_factor: bool. 是否保持功率因子不变。
    :param factor_sigma: float. 随机功率因子变化率的方差，变化率~N(1.0, factor_sigma)。
                         or None. 不随机变化。
    :param clip: bool. 是否保持有功/无功在限值之内。
    """
    sub = loads[(loads['mark'] == 1) & (loads['p0'] > 0.)]
    if indices:
        indices = [i for i in indices if i in sub.index]
        sub = sub.loc[indices]
    if keep_factor:
        factor = sub['q0'] / sub['p0']
        if factor_sigma:
            factor *= np.random.normal(loc=1.0, scale=factor_sigma, size=(len(factor),))
    ratio = sub['p0']
    if p_sigma:
        ratio *= np.random.normal(loc=1.0, scale=p_sigma, size=(len(ratio),))
    loads.loc[sub.index, 'p0'] = sub['p0'] + ratio / np.sum(ratio) * delta
    if clip:
        loads['p0'] = np.clip(loads['p0'], loads['pmin'], loads['pmax'])
    if keep_factor:
        loads.loc[sub.index, 'q0'] = loads.loc[sub.index, 'p0'] * factor
        if clip:
            loads['q0'] = np.clip(loads['q0'], loads['qmin'], loads['qmax'])


def random_load_q0(loads, sigma, clip=False):
    """ 修改负荷表的无功初值，修改负荷表q0列。

    :param loads: pd.DataFrame. 负荷数据表。
    :param sigma: float. 随机无功变化率的方差，变化率~N(1.0, sigma)；
                  or None. 在无功限值内以均匀分布进行采样。
    :param clip: bool. 是否保持无功在限值之内。
    """
    if sigma is not None:
        loads['q0'] *= np.random.normal(loc=1.0, scale=sigma, size=(loads.shape[0],))
    else:
        loads['q0'] = loads['qmin'] + \
                      np.random.rand(loads.shape[0]) * (loads['qmax'] - loads['qmin'])
    if clip:
        loads['q0'] = np.clip(loads['q0'], loads['qmin'], loads['qmax'])


def set_gl_p0(data, value, keep_factor=True, clip=True):
    """ 设置机组或负荷的有功初值，修改机组表或负荷表的p0.

    :param data: pd.DataFrame. 机组表或负荷表。
    :param value: np.array. 全部有功数值。
    :param keep_factor: bool. 是否保持功率因子不变。
    :param clip: bool. 是否保持有功和无功在限值以内。
    """
    if keep_factor:
        factor = data['q0'] / (data['p0'] + EPS)
    data['p0'] = value
    if keep_factor:
        data['q0'] = data['p0'] * factor
    if clip:
        data['p0'] = np.clip(data['p0'], data['pmin'], data['pmax'])
        data['q0'] = np.clip(data['q0'], data['qmin'], data['qmax'])


def full_open_generators(generators, indices, v0=None):
    """ 开机并满发。

    :param generators: pd.DataFrame. 机组数据表。
    :param indices: list. 指定机组的索引列表；
    :param v0: float or [float]. 设置电压初值；
               or None. 不修改电压初值。
    """
    generators.loc[indices, 'mark'] = 1
    generators.loc[indices, 'p0'] = generators.loc[indices, 'pmax']
    if v0 is not None:
        generators.loc[indices, 'v0'] = v0


def close_all_branches(data):
    """ 闭合所有交流线和变压器支路。

    :param data: dict of pd.DataFrame. 数据集合。
    """
    data['acline']['mark'] = 1
    data['transformer']['mark'] = 1


def random_open_acline(power, num, keep_link=True):
    """ 随机开断一定数量的交流线。

    :param power: Power. Power实例，需要检查连通性。
    :param num: int. 开断数量。
    :param keep_link: bool. 是否保持连接状态，即不增加分岛。
    :return list[index]. 开断线路的索引列表。
    """
    ret = []
    aclines = power.data['acline']
    indices = aclines[(aclines['mark'] == 1) & (aclines['ibus'] != aclines['jbus'])].index
    if keep_link:
        graph = PowerGraph(power, graph_type='multi', node_type='bus', on_only=True)
    while num > 0:
        if len(indices) < num:
            raise ValueError('Not plenty of aclines to be off.')
        idx = indices[random.sample(range(len(indices)), 1)[0]]
        indices = indices.drop(idx)
        if keep_link:
            edge = (aclines.loc[idx, 'ibus'], aclines.loc[idx, 'jbus'], idx)
            if graph.is_connected(edge[0], edge[1], [edge]):
                continue
            graph.G.remove_edge(*edge)
        aclines.loc[idx, 'mark'] = 0
        ret.append(idx)
        num = num - 1
    return ret


def load_actions(power, base_path, out_path, files, fmt='on', st=True, wmlf=True):
    """ 从文件加载并执行修改动作

    :param power: Power. Power实例。
    :param base_path: str. 基础数据目录。
    :param out_path: str. 输出目录，输入的动作文件也在这里。
    :param files: [str]. 文件列表。
    :param fmt: str. 数据格式类型。
    :param st: bool. 是否输出ST*文件。
    :param wmlf: bool. 是否计算潮流。
    :return dict. 每个文件对应目录的输出是否成功（潮流是否收敛）
    """
    ret = {}
    if not power:
        power = Power(fmt=fmt)
        power.load_power(base_path, fmt=fmt, lp=False, st=st)
        power.set_index(idx='name')
    for f in files:
        actions = pd.read_csv(os.path.join(out_path, f), encoding='gbk', index_col=False)
        for _, etype, idx, dtype, value in actions.itertuples():
            set_values(power.data, etype, dtype, {idx: value})
        if '.' in f:
            name = f[:f.index('.')]
        path = os.path.join(out_path, name)
        power.save_power(path, fmt=power.fmt, lp=False, st=st)
        shutil.copy(os.path.join(base_path, 'LF.L0'), path)
        if st:
            shutil.copy(os.path.join(base_path, 'ST.S0'), path)
        if wmlf:
            call_wmlf(path)
            ret[name] = check_lfcal(path)
        else:
            ret[name] = True
    return ret


if __name__ == '__main__':
    base_path = os.path.join(os.path.expanduser('~'), 'data', 'wepri36', 'wepri36')
    out_path = os.path.join(os.path.expanduser('~'), 'data', 'wepri36', 'actions')
    fmt = 'off'
    power = Power(fmt)
    power.load_power(base_path, fmt=fmt, lp=False, st=False)
    power.set_index(idx='name')
    ret = load_actions(power, base_path, out_path, files=['1.txt', '2.txt'], st=False, wmlf=True)