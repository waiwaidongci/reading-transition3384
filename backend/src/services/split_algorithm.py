"""会话切分：练习集 / 验收集的防泄漏分组与精确平衡搜索。

纯函数模块，只依赖标准库，便于独立复核：
- build_groups      同一路线、共享连续三章、同一会话编号的会话归为一组（传递闭包）。
- plan_groups       统计每组在各文集的人数与各文集的合法人数区间。
- quick_infeasible  能直接证明的无解情形，返回中文理由。
- solve_split       精确搜索，支持时间预算、进度保存与续跑；只有穷尽后才报无解。
- build_result      展开逐会话结果并再次核验，核验失败拒绝交付。

搜索分两层：只涉及一个文集的“本地组”用子集和位集精确判定与还原；
跨越多个文集的“跨文集组”数量有限，用带回溯的深度优先搜索枚举其归属，
每个节点用各文集的本地可达人数区间剪枝。两层都是精确判定，因此无解是证明结果，
超时则保存搜索栈并进入待定。
"""
from collections import defaultdict
import hashlib, json, time

TRAIN = 'train'
VALIDATION = 'validation'
SIDE_LABELS = {TRAIN: '练习集', VALIDATION: '验收集'}


def _path_parts(path):
    return [part.strip() for part in str(path or '').split('>')]


def _link_key(session):
    value = session.get('session')
    return str(value).strip() if value is not None else ''


def build_groups(sessions):
    """把必须同侧的会话并组，返回按首次出现顺序编号的分组。"""
    parent = list(range(len(sessions)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    by_route, by_trigram, by_session = {}, {}, {}
    for index, session in enumerate(sessions):
        parts = tuple(_path_parts(session.get('path')))
        if parts in by_route:
            union(index, by_route[parts])
        else:
            by_route[parts] = index
        for start in range(len(parts) - 2):
            key = parts[start:start + 3]
            if key in by_trigram:
                union(index, by_trigram[key])
            else:
                by_trigram[key] = index
        sid = _link_key(session)
        if sid:
            if sid in by_session:
                union(index, by_session[sid])
            else:
                by_session[sid] = index

    buckets = defaultdict(list)
    for index in range(len(sessions)):
        buckets[find(index)].append(index)
    ordered = sorted(buckets)
    return [{'id': 'G%04d' % (position + 1), 'members': buckets[root]}
            for position, root in enumerate(ordered)]


def plan_groups(sessions, collections):
    """整理分组、逐文集合计与合法人数区间。

    练习集人数 train 必须满足 floor(N/2) <= train <= ceil(N/2)，
    等价于验收集人数 N-train 也落在同一区间，即两侧人数最多差一；
    同时两侧都必须至少各有一人，因此再收紧为 [max(1,floor(N/2)), min(N-1,ceil(N/2))]。
    会话数不足 2 的文集该区间为空，由 quick_infeasible 直接报告无解。
    """
    collection_ids = []
    for collection in collections:
        if collection['id'] not in collection_ids:
            collection_ids.append(collection['id'])
    meta = {c['id']: {'collection_id': c['id'], 'key': c.get('key', ''), 'name': c.get('name', '')}
            for c in collections}
    totals = {cid: 0 for cid in collection_ids}
    for session in sessions:
        cid = session['collection_id']
        if cid not in totals:
            raise ValueError('会话所属文集不在本次切分范围内')
        totals[cid] += 1
    groups = build_groups(sessions)
    for group in groups:
        counts = defaultdict(int)
        for index in group['members']:
            counts[sessions[index]['collection_id']] += 1
        group['counts'] = {cid: counts.get(cid, 0) for cid in collection_ids}
        group['size'] = len(group['members'])
        group['touched'] = [cid for cid in collection_ids if counts.get(cid, 0)]
    return {
        'sessions': sessions,
        'groups': groups,
        'collection_ids': collection_ids,
        'meta': meta,
        'totals': totals,
        'lower': {cid: max(1, totals[cid] // 2) for cid in collection_ids},
        'upper': {cid: min(totals[cid] - 1, (totals[cid] + 1) // 2) for cid in collection_ids},
    }


def quick_infeasible(plan):
    """可直接证明的无解情形；返回中文理由，或 None 表示仍需搜索。"""
    totals, upper, meta, groups = plan['totals'], plan['upper'], plan['meta'], plan['groups']
    if not plan['collection_ids']:
        return '未选择任何文集'
    if not groups:
        return '所选文集没有会话，无法在练习集与验收集两侧都安排会话'
    for cid in plan['collection_ids']:
        name = meta[cid]['name'] or ('文集 #%s' % cid)
        if totals[cid] < 2:
            return '文集「%s」只有 %d 个会话，无法在两侧都有人' % (name, totals[cid])
        for group in groups:
            count = group['counts'][cid]
            if count == totals[cid]:
                return ('文集「%s」的全部 %d 个会话同属一组 %s，组内会话只能同侧，无法分到两侧'
                        % (name, totals[cid], group['id']))
            if count > upper[cid]:
                return ('分组 %s 在文集「%s」中有 %d 个会话，超过任一侧允许的最多 %d 个'
                        % (group['id'], name, count, upper[cid]))
    return None


# ---------- 本地组：子集和位集 ----------

def _reach_bits(values, cap):
    """values 的任意子集和（不超过 cap）位集，第 i 位表示和为 i 可达。"""
    bits = 1
    mask = (1 << (cap + 1)) - 1
    for value in values:
        if value <= 0:
            continue
        bits |= (bits << value) & mask
    return bits


def _range_mask(lo, hi):
    if lo < 0:
        lo = 0
    if hi < lo:
        return 0
    return ((1 << (hi - lo + 1)) - 1) << lo


def _subset_for(groups, cid, lo, hi):
    """在本地组中找一个归属练习集的子集，使其在文集 cid 的人数和落在 [lo, hi]。"""
    if lo <= 0 <= hi:
        return set()
    reach = {0: None}
    for group in groups:
        value = group['counts'][cid]
        if value <= 0:
            continue
        for total in sorted(reach, reverse=True):
            nxt = total + value
            if nxt > hi:
                continue
            if nxt not in reach:
                reach[nxt] = (total, group['id'])
    for target in sorted(reach):
        if lo <= target <= hi:
            chosen, cursor = set(), target
            while cursor:
                previous, gid = reach[cursor]
                chosen.add(gid)
                cursor = previous
            return chosen
    return None


# ---------- 进度保存 ----------

def _checkpoint(order, decisions, frames, nodes):
    return {'order': [group['id'] for group in order],
            'decisions': list(decisions), 'frames': list(frames), 'nodes': nodes}


def _restore(checkpoint, order):
    """读取已保存的搜索进度；结构不符时从空进度重新开始。"""
    if not checkpoint:
        return [], [0], 0, ''
    try:
        if checkpoint.get('order') != [group['id'] for group in order]:
            raise ValueError('分组顺序已变化')
        decisions = [int(x) for x in checkpoint.get('decisions', [])]
        frames = [int(x) for x in checkpoint.get('frames', [])]
        nodes = int(checkpoint.get('nodes', 0))
        if len(frames) != len(decisions) + 1 or nodes < 0:
            raise ValueError('进度结构不完整')
        if any(d not in (0, 1) for d in decisions):
            raise ValueError('进度决策非法')
        if any(f not in (0, 1, 2) for f in frames):
            raise ValueError('进度搜索栈非法')
        return decisions, frames, nodes, 'resumed'
    except (TypeError, ValueError, KeyError):
        return [], [0], 0, ''


def _split_groups(plan):
    linked, local = [], defaultdict(list)
    for group in plan['groups']:
        if len(group['touched']) >= 2:
            linked.append(group)
        else:
            local[group['touched'][0] if group['touched'] else plan['collection_ids'][0]].append(group)
    return linked, local


def solve_split(plan, budget, checkpoint=None, save=None, clock=time.monotonic):
    """精确搜索二侧切分。

    返回 status:
      solved      找到并已核验的切分（展开见 build_result）
      infeasible  搜索树全部穷尽，证实无解
      pending     超出时间预算，progress 中保存搜索栈，可原样续跑
    """
    cids = plan['collection_ids']
    linked, local = _split_groups(plan)
    order = sorted(linked, key=lambda group: (-group['size'], group['id']))
    count = len(order)
    suffix = [{cid: 0 for cid in cids} for _ in range(count + 1)]
    for position in range(count - 1, -1, -1):
        for cid in cids:
            suffix[position][cid] = suffix[position + 1][cid] + order[position]['counts'][cid]
    local_bits = {cid: _reach_bits([group['counts'][cid] for group in local[cid]], plan['upper'][cid])
                  for cid in cids}

    def feasible_rest(sums, depth):
        """剩余跨文集组与本地组能否把每个文集补进合法区间。"""
        for cid in cids:
            lo = plan['lower'][cid] - sums[cid] - suffix[depth][cid]
            hi = plan['upper'][cid] - sums[cid]
            if hi < 0 or _range_mask(lo, hi) & local_bits[cid] == 0:
                return False
        return True

    decisions, frames, nodes, restored = _restore(checkpoint, order)
    sums = {cid: 0 for cid in cids}
    for position, choice in enumerate(decisions):
        if choice == 0:
            for cid in cids:
                sums[cid] += order[position]['counts'][cid]

    deadline = clock() + max(0.0, float(budget))
    last_save = clock()
    while True:
        now = clock()
        if now > deadline:
            progress = _checkpoint(order, decisions, frames, nodes)
            if save is not None:
                save(progress)
            return {'status': 'pending', 'progress': progress, 'nodes': nodes,
                    'order': [group['id'] for group in order], 'restored': restored}
        if save is not None and now - last_save > 2.0:
            save(_checkpoint(order, decisions, frames, nodes))
            last_save = now

        depth = len(decisions)
        if depth == count:
            if feasible_rest(sums, depth):
                return {'status': 'solved', 'order': [group['id'] for group in order],
                        'decisions': list(decisions), 'linked_local_sums': dict(sums),
                        'nodes': nodes, 'restored': restored}
            if not decisions:
                return {'status': 'infeasible', 'nodes': nodes,
                        'order': [group['id'] for group in order], 'restored': restored}
            frames.pop()
            choice = decisions.pop()
            if choice == 0:
                for cid in cids:
                    sums[cid] -= order[depth - 1]['counts'][cid]
            continue

        # 每组的练习/验收选择左右对称，固定首组进练习集以消除镜像搜索。
        choice, limit = frames[-1], (0 if depth == 0 else 1)
        if choice > limit:
            frames.pop()
            if depth == 0:
                return {'status': 'infeasible', 'nodes': nodes,
                        'order': [group['id'] for group in order], 'restored': restored}
            taken = decisions.pop()
            if taken == 0:
                for cid in cids:
                    sums[cid] -= order[depth - 1]['counts'][cid]
            continue

        frames[-1] = choice + 1
        if choice == 0:
            for cid in cids:
                sums[cid] += order[depth]['counts'][cid]
        decisions.append(choice)
        nodes += 1
        if not feasible_rest(sums, depth + 1):
            decisions.pop()
            if choice == 0:
                for cid in cids:
                    sums[cid] -= order[depth]['counts'][cid]
            continue
        frames.append(0)


def materialize(plan, outcome):
    """把搜索解还原成每组归属；本地组在此处才展开为具体子集。"""
    linked, local = _split_groups(plan)
    by_id = {group['id']: group for group in linked}
    side = {}
    for gid, choice in zip(outcome['order'], outcome['decisions']):
        if gid not in by_id:
            raise ValueError('切分结果引用了未知分组')
        side[gid] = TRAIN if choice == 0 else VALIDATION
    sums = outcome.get('linked_local_sums', {})
    for cid in plan['collection_ids']:
        base = sums.get(cid, 0)
        lo = plan['lower'][cid] - base
        hi = plan['upper'][cid] - base
        chosen = _subset_for(local[cid], cid, lo, hi)
        if chosen is None:
            raise ValueError('本地组无法补足文集「%s」的目标人数' % (plan['meta'][cid]['name'] or cid))
        for group in local[cid]:
            side[group['id']] = TRAIN if group['id'] in chosen else VALIDATION
    if len(side) != len(plan['groups']):
        raise ValueError('切分结果分组不完整')
    return side


def _leaks(rows):
    """独立复核：同一条路线、同一个连续三章、同一个会话编号不得跨两侧。"""
    seen = {'route': defaultdict(set), 'trigram': defaultdict(set), 'session': defaultdict(set)}
    for row in rows:
        parts = _path_parts(row.get('path'))
        seen['route'][tuple(parts)].add(row['side'])
        for start in range(len(parts) - 2):
            seen['trigram'][tuple(parts[start:start + 3])].add(row['side'])
        sid = _link_key(row)
        if sid:
            seen['session'][sid].add(row['side'])
    return {name: sorted(key for key, sides in keys.items() if len(sides) > 1)
            for name, keys in seen.items()}


def build_result(plan, side, nodes):
    """按每组归属展开逐会话切分，并重新核验全部约束。"""
    if set(side) != {group['id'] for group in plan['groups']}:
        raise ValueError('切分结果分组不完整')
    rows = []
    for group in plan['groups']:
        chosen = side[group['id']]
        for index in group['members']:
            session = plan['sessions'][index]
            rows.append({
                'group': group['id'], 'side': chosen, 'side_label': SIDE_LABELS[chosen],
                'collection': session.get('collection_name', ''), 'collection_id': session['collection_id'],
                'reference': session.get('reference', ''), 'session': session.get('session', ''),
                'path': session.get('path', ''),
            })

    train_of, validation_of = defaultdict(int), defaultdict(int)
    for row in rows:
        if row['side'] == TRAIN:
            train_of[row['collection_id']] += 1
        else:
            validation_of[row['collection_id']] += 1

    collections, balance_ok, both_sides = [], True, True
    for cid in plan['collection_ids']:
        train, validation = train_of[cid], validation_of[cid]
        total = plan['totals'][cid]
        if train + validation != total:
            raise ValueError('切分结果会话覆盖不完整')
        difference = abs(train - validation)
        balance_ok = balance_ok and difference <= 1
        both_sides = both_sides and train >= 1 and validation >= 1
        item = dict(plan['meta'][cid])
        item.update({'total': total, 'train': train, 'validation': validation, 'difference': difference})
        collections.append(item)

    group_rows = []
    for group in plan['groups']:
        members = [row for row in rows if row['group'] == group['id']]
        group_rows.append({'id': group['id'], 'session_count': len(members),
                           'side': members[0]['side_label'] if members else '',
                           'train': sum(1 for row in members if row['side'] == TRAIN),
                           'validation': sum(1 for row in members if row['side'] == VALIDATION),
                           'collections': '、'.join(sorted({row['collection'] for row in members}))})

    leaks = _leaks(rows)
    checks = {'coverage': len(rows) == len(plan['sessions']),
              'group_integrity': all(len({row['side'] for row in rows if row['group'] == g['id']}) == 1
                                     for g in plan['groups']),
              'balance': balance_ok, 'both_sides': both_sides,
              'no_route_leak': not leaks['route'], 'no_trigram_leak': not leaks['trigram'],
              'no_session_leak': not leaks['session']}
    checks['verified'] = all(checks.values())
    if not checks['verified']:
        failed = '，'.join(name for name, passed in checks.items() if passed is False)
        raise ValueError('切分结果未通过核验：' + failed)

    rows.sort(key=lambda row: (row['group'], row['collection_id'], row['reference']))
    signature = sorted((str(row['collection_id']), str(row['reference']), row['group'], row['side'])
                       for row in rows)
    checksum = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
    train_total = sum(item['train'] for item in collections)
    validation_total = sum(item['validation'] for item in collections)
    return {
        'rows': rows,
        'collections': collections,
        'groups': group_rows,
        'checks': checks,
        'checksum': checksum,
        'nodes': nodes,
        'summary': [{'label': '会话总数', 'value': len(rows)},
                    {'label': '练习集', 'value': train_total},
                    {'label': '验收集', 'value': validation_total},
                    {'label': '分组数', 'value': len(group_rows)}],
        'columns': ['group', 'side_label', 'collection', 'reference', 'session', 'path'],
        'explanation': ('同一路线、共享连续三章（含间接相连）或同一会话编号的会话并入一组，'
                        '组内会话只能同侧；每组整体进入练习集或验收集，每个文集两侧都有人且人数相差不超过一。'
                        '切分只依据会话内容，不使用任何结果字段。'),
    }
