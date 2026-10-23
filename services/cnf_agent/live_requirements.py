"""Source and scope checks before F3a may inspect or mutate the real fixture."""
import re

from .readonly import numeric_sources_valid


LABELS = {'user_slot': '用户对象', 'direction': '传输方向', 'protocol': '实验协议',
          'min_bandwidth_mbps': '带宽下界', 'max_bandwidth_mbps': '带宽上界',
          'priority': '优先级', 'valid_for_seconds': '实验有效时长',
          'measurement_window_seconds': '测量窗口'}
UNITS = r'(Mbps|Gbps|Kbps|kbps|Mb/s|MB/s|兆比特/秒)'
NUMBER = r'(\d+(?:\.\d+)?)'
UE_MENTION = re.compile(r'(?<![a-z0-9])ue\s*(\d+)(?![a-z0-9])', flags=re.I)
TIME_ROLES = {
    'valid_for_seconds': r'(?:实验(?:时长)?|运行(?:时长)?|持续|有效(?:时长|期)|duration|valid_for_seconds)',
    'measurement_window_seconds': r'(?:测量窗口|测量时长|统计窗口|measurement_window(?:_seconds)?)',
}


def bound_matches(quote, value, lower):
    marker = r'(?:至少|最低|保底|下界|minimum|min)' if lower else r'(?:上限|最高|最多|不超过|最大|上界|封顶|maximum|max)'
    matches = re.findall(marker + r'[^\d]{0,12}' + NUMBER + r'\s*' + UNITS, quote)
    matches += re.findall(NUMBER + r'\s*' + UNITS + r'[^\d]{0,4}' + marker, quote)
    return any(abs(float(number) * {'Gbps': 1000, 'Kbps': .001, 'kbps': .001, 'MB/s': 8}.get(unit, 1) - value) < 1e-8
               for number, unit in matches)


def time_matches(quote, value, field):
    pattern = TIME_ROLES[field] + r'\s*(?:为|是|[:：=])?\s*' + NUMBER + r'\s*(分钟|秒|min|s)'
    return any(abs(float(number) * (60 if unit in ('分钟', 'min') else 1) - value) < 1e-8
               for number, unit in re.findall(pattern, quote, flags=re.I))


def user_clauses(text):
    mentions = list(UE_MENTION.finditer(text))
    for index, mention in enumerate(mentions):
        end = mentions[index + 1].start() if index + 1 < len(mentions) else len(text)
        yield 'ue' + mention[1], text[mention.start():end]


def belongs_to_user(source, slot, texts):
    """A per-user target quote must fit wholly inside that user's explicit clause.

    The next UE mention ends the clause. Shared/implicit subjects are left for
    clarification rather than inferred by this execution gate.
    """
    return any(owner == slot and source.quote in clause for owner, clause in user_clauses(texts[source.message_id]))


def common_or_owned(source, slot, texts, role):
    if belongs_to_user(source, slot, texts):
        return True
    text = texts[source.message_id]
    first = UE_MENTION.search(text)
    common = text[:first.start()] if first else text
    # Global setup may supply common protocol/direction/timing, unless that user
    # has an explicit per-user statement for the same role.
    return source.quote in common and not any(
        owner == slot and re.search(role, clause, flags=re.I)
        for message in texts.values() for owner, clause in user_clauses(message))


def assess(requirement, messages):
    """Return ready/clarify/rejected; no defaults or user/session substitution."""
    texts = {message.id: message.text for message in messages}
    sources = [s for request in requirement.requests for s in request.sources.values()]
    sources += list(requirement.unsupported_goals)
    if requirement.scope_source:
        sources.append(requirement.scope_source)
    if any(source.message_id not in texts or source.quote not in texts[source.message_id] for source in sources):
        return 'rejected', ('需求字段引用不属于用户原话',)
    if requirement.unsupported_goals:
        return 'rejected', ('包含当前 E3 范围外的目标；未执行',)
    if requirement.ambiguities:
        return 'clarify', requirement.ambiguities
    if requirement.scope is None:
        return 'clarify', ('请明确这是 C1 受控下行实验，而非持续业务保障',)
    if not re.search(r'(?:受控.*下行|C1.*下行|下行.*受控)', requirement.scope_source.quote, flags=re.I):
        return 'clarify', ('实验范围缺少明确的受控下行原话',)
    if len(requirement.requests) != 2:
        return 'clarify', ('需要 UE1、UE2 两名用户各自的完整目标',)
    slots = [request.user_slot for request in requirement.requests]
    if any(slot is None for slot in slots):
        return 'clarify', ('两名用户对象都必须明确',)
    if set(slots) != {'ue1', 'ue2'}:
        return 'rejected', ('只支持两个不同的既有用户 UE1、UE2',)
    issues = []
    for request in requirement.requests:
        issues += [request.user_slot + '：' + LABELS[key] for key in request.missing()]
    if issues:
        return 'clarify', tuple(issues)
    for request in requirement.requests:
        if not all(belongs_to_user(request.sources[key], request.user_slot, texts)
                   for key in ('min_bandwidth_mbps', 'max_bandwidth_mbps', 'priority')):
            return 'clarify', ('带宽和优先级原话必须明确归属于对应 UE；请逐用户说明',)
        if request.min_bandwidth_mbps > request.max_bandwidth_mbps:
            return 'clarify', ('带宽下界超过上界；不能自动降低需求',)
        if request.direction != 'downlink' or request.protocol != 'UDP':
            return 'rejected', ('F3a 仅支持既有 C1 UDP 下行实验',)
        if request.valid_for_seconds != 30 or request.measurement_window_seconds != 20:
            return 'rejected', ('现有工具仅验收 30 秒实验与 20 秒候选稳态窗口',)
        if any(getattr(request, key) is not None for key in ('destination', 'max_latency_ms', 'max_loss_percent')):
            return 'rejected', ('当前工具不能执行目的地选择、时延或丢包保障',)
        if not numeric_sources_valid(request):
            return 'clarify', ('数值目标与引用单位不一致',)
        if not all(bound_matches(request.sources[key].quote, getattr(request, key), key.startswith('min_'))
                   for key in ('min_bandwidth_mbps', 'max_bandwidth_mbps')):
            return 'clarify', ('带宽上、下界的数值和语义须分别明确',)
        if not all(time_matches(request.sources[key].quote, getattr(request, key), key)
                   for key in ('valid_for_seconds', 'measurement_window_seconds')):
            return 'clarify', ('实验时长与测量窗口须分别具有对应角色及秒数来源',)
        roles = dict(TIME_ROLES, direction=r'上行|下行|上传|下载|uplink|downlink', protocol=r'UDP|TCP')
        if not all(common_or_owned(request.sources[key], request.user_slot, texts, role) for key, role in roles.items()):
            return 'clarify', ('方向、协议和时长必须来自共同设置或对应 UE，不能借用其他用户目标',)
        slot_quote = request.sources['user_slot'].quote.lower()
        priority_patterns = {'high': r'高优先|优先保障|优先于', 'normal': r'普通|正常|同等|公平', 'low': r'低优先|次要|让步'}
        slot_pattern = r'(?<![a-z0-9])ue\s*' + request.user_slot[-1] + r'(?!\d)'
        if (not re.search(slot_pattern, slot_quote)
                or 'UDP' not in request.sources['protocol'].quote.upper()
                or not any(word in request.sources['direction'].quote.lower() for word in ('下行', '下载', 'downlink'))
                or not re.search(priority_patterns[request.priority], request.sources['priority'].quote)):
            return 'clarify', ('用户、方向、协议或优先级的原话依据不足',)
    return 'ready', ()
