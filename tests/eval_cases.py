"""Behavioural evals with synthetic data. Check facts/actions, not exact model phrasing."""
from dataclasses import dataclass
from datetime import timedelta
import math
import re

SUCCESS = re.compile(r'\b(klart|lagt till|lade till|la till|ligger (nu )?i|är tillag[dt]|tillag[dt]|har lagts|bokat|inlag[dt])\b', re.I)
FAILURE = re.compile(r'(kunde inte|gick (tyvärr )?inte|misslyck|lyckades inte|fel\b|svarade inte|inte svarade|inte skapa)', re.I)
NEGATED = re.compile(r'\b(inte|ej|aldrig)\s+(\w+\s+)?(klart|lagt till|lade till|la till|ligger (nu )?i|är tillagd|har lagts|bokat|inlagd)\b', re.I)


def claims_success(reply):
    return bool(SUCCESS.search(NEGATED.sub('', reply)))


@dataclass(frozen=True)
class Turn:
    text: str
    mode: str = 'ok'
    date: str | None = None
    add_title: str | None = None
    add_start: str | None = None
    calendar: str | None = None
    power: bool | None = None
    contains: str | None = None
    failed: bool = False
    no_writes: bool = True
    question: bool = False
    no_calls: bool = False

    def grade(self, calls, reply):
        adds = [c for c in calls if c['tool'] == 'calendar_add_event']
        powers = [c for c in calls if c['tool'] == 'tv_power']
        checks = [('nonempty reply', bool(reply.strip()))]
        if self.no_writes:
            checks.append(('no calendar/TV writes', not adds and not powers))
        if self.no_calls:
            checks.append(('no tool calls', not calls))
        if self.date:
            checks.append(('reads correct date', any(c['tool'] == 'calendar_list_events' and
                           c['args'].get('date') == self.date for c in calls)))
        if self.add_title:
            checks += [('adds exactly once', len(adds) == 1),
                       ('only add/read tools', all(c['tool'] in ('calendar_add_event', 'calendar_list_events') for c in calls)),
                       ('correct title', len(adds) == 1 and self.add_title.casefold() in adds[0]['args'].get('title', '').casefold()),
                       ('correct start', len(adds) == 1 and adds[0]['args'].get('start', '').startswith(self.add_start))]
            if self.calendar:
                checks.append(('correct calendar', len(adds) == 1 and adds[0]['args'].get('calendar') == self.calendar))
            if not self.failed:
                checks.append(('confirms success', claims_success(reply) and not FAILURE.search(reply)))
        if self.power is not None:
            checks.append(('exact TV action', [(c['tool'], c['args'].get('on')) for c in calls] == [('tv_power', self.power)]))
        if self.failed:
            checks += [('never claims success', not claims_success(reply)), ('reports failure', bool(FAILURE.search(reply)))]
        if self.contains:
            checks.append(('required answer facts', bool(re.search(self.contains, reply, re.I))))
        if self.question:
            checks.append(('asks for clarification/approval', reply.rstrip().endswith('?')))
        return [name for name, passed in checks if not passed]


@dataclass(frozen=True)
class Scenario:
    name: str
    turns: tuple[Turn, ...]


def scenarios(now):
    today = now.date()
    tomorrow = today + timedelta(days=1)
    friday = today + timedelta(days=(4 - today.weekday()) % 7 or 7)
    return [
        Scenario('add-today', (Turn('Lägg till köp kvarg idag kl 17 i kalendern', add_title='kvarg',
            add_start=f'{today}T17:00', no_writes=False),)),
        Scenario('failed-booking', (Turn('Boka tandläkaren på fredag klockan tio', mode='fail',
            add_title='tandläkar', add_start=f'{friday}T10:00', failed=True, no_writes=False),)),
        Scenario('calendar-tomorrow', (Turn('Vad har jag i kalendern i morgon?', date=str(tomorrow), contains='standup|tvätt'),)),
        Scenario('tv-off', (Turn('Stäng av tv:n', power=False, no_writes=False),)),
        Scenario('outing-needs-approval', (Turn('Jag vill till Moderna museet på söndag och vara där klockan 13', question=True),)),
        Scenario('days-to-christmas', (Turn('Hur många dagar är det kvar till julafton?',
            contains=rf'\b{(today.replace(year=today.year + (today.month == 12 and today.day > 24), month=12, day=24) - today).days}\b',
            no_calls=True),)),
        Scenario('calendar-followup', (Turn('Vad har jag i kalendern idag?', date=str(today)),
            Turn('Och imorgon?', date=str(tomorrow), contains='standup|tvätt'))),
        Scenario('empty-calendar', (Turn('Vad har jag i kalendern imorgon?', mode='empty', date=str(tomorrow),
            contains='inget|inga|tom|ledig|obokad'),)),
        Scenario('failed-calendar-read', (Turn('Vad har jag i kalendern imorgon?', mode='fail-read',
            date=str(tomorrow), failed=True),)),
        Scenario('slow-calendar-read', (Turn('Vad har jag i kalendern imorgon?', mode='slow',
            date=str(tomorrow), contains='standup|tvätt'),)),
        Scenario('tv-on', (Turn('Sätt på tv:n', power=True, no_writes=False),)),
        Scenario('explicit-calendar', (Turn('Lägg till Projektmöte i Jobb i morgon klockan 14',
            add_title='Projektmöte', add_start=f'{tomorrow}T14:00', calendar='Jobb', no_writes=False),)),
        Scenario('time-needs-clarification', (Turn('Boka tandläkaren imorgon, fråga mig om klockslag först.', question=True),)),
        Scenario('english-calendar', (Turn('What is on my calendar tomorrow? Answer in English.',
            date=str(tomorrow), contains='standup|laundry|tvätt'),)),
    ]


def select(cases, names, repeat, max_turns):
    if repeat < 1 or max_turns < 1:
        raise ValueError('repeat and max-turns must be positive')
    unknown = set(names or ()) - {case.name for case in cases}
    if unknown:
        raise ValueError('unknown cases: ' + ', '.join(sorted(unknown)))
    selected = [case for case in cases if not names or case.name in names] * repeat
    turns = sum(len(case.turns) for case in selected)
    if turns > max_turns:
        raise ValueError(f'{turns} question turns requested, budget is {max_turns}; select fewer cases or raise --max-turns')
    return selected


def percentile(values, p):
    values = sorted(float(v) for v in values if isinstance(v, (int, float)) and math.isfinite(v))
    return values[max(0, math.ceil(len(values) * p) - 1)] if values else None


def summarize(rows):
    costs = [r.get('cost_usd') for r in rows]
    complete_cost = bool(costs) and all(isinstance(v, (int, float)) and math.isfinite(v) for v in costs)
    return {'turns': len(rows), 'failed_turns': sum(bool(r['failed_checks']) for r in rows),
            'first_token_p50_s': percentile([r.get('first_token_s') for r in rows], .5),
            'first_token_p95_s': percentile([r.get('first_token_s') for r in rows], .95),
            'wall_p50_s': percentile([r.get('wall_s') for r in rows], .5),
            'wall_p95_s': percentile([r.get('wall_s') for r in rows], .95),
            'reported_cost_usd': round(sum(costs), 6) if complete_cost else None,
            'cost_missing_turns': sum(v is None for v in costs)}


# Ad-hoc prompts (integration_test.py --prompt/--prompts) run only these tools for real: public lookups that only
# read, the mock calendar's read and the screen card, which only returns with the reply. Every other tool the agent
# offers (calendar add, TV, radio, memory notes, reminders, the phone push, any tool added later) is replaced by a
# recording stub in tests/eval_server.py, so a new write tool is blocked until it is added here deliberately.
PROMPT_READ_TOOLS = frozenset({'vader', 'wikipedia', 'nyheter', 'flygplan', 'iss', 'polisen', 'resa', 'webbsok',
                               'calendar_list_events', 'skarm'})


def read_prompts(texts=(), files=()):
    """Prompts from --prompt values and --prompts files (one per line; blank lines and # comments are skipped)."""
    prompts = []
    for text in texts or ():
        if not text.strip():
            raise ValueError('--prompt needs text')
        prompts.append(text.strip())
    for name in files or ():
        with open(name, encoding='utf-8') as f:
            prompts += [line.strip() for line in f if line.strip() and not line.lstrip().startswith('#')]
    return prompts


def prompt_scenarios(prompts, chain=False):
    """One independent scenario per prompt (fresh history each), or one conversation with --chain."""
    turns = tuple(Turn(text, no_writes=False) for text in prompts)
    if chain:
        return [Scenario('prompts', turns)] if turns else []
    return [Scenario(f'prompt-{n}', (turn,)) for n, turn in enumerate(turns, 1)]
