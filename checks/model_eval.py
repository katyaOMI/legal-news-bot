"""Check saved responses locally. CLI exit: 0 valid, 1 invalid response, 2 input/I/O error."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path


DECISIONS = ('accept', 'reject', 'review')
FIELDS = {'id', 'decision', 'reason', 'evidence', 'russia_basis'}


class InputError(ValueError):
    """Invalid evaluation configuration; never include input payloads in errors."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_json_key')
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError('nonfinite_json_number')


def decode(raw):
    return json.loads(raw, object_pairs_hook=_unique_object,
                      parse_constant=_invalid_constant)


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _has_quote(quote, example):
    if not _nonempty(quote):
        return False
    quote = quote.replace('\r\n', '\n')
    return any(quote in example[field].replace('\r\n', '\n')
               for field in ('title', 'text'))


def prepare_inputs(examples, gold, selected_ids=None):
    """Validate and select a fixed subset; extra original dataset rows stay out."""
    if not isinstance(examples, list) or not examples:
        raise InputError('invalid_examples')
    index = {}
    for row in examples:
        if (not isinstance(row, dict) or not _nonempty(row.get('id'))
                or not _nonempty(row.get('title'))
                or not isinstance(row.get('text'), str)
                or row['id'] in index):
            raise InputError('invalid_examples')
        index[row['id']] = row
    if not isinstance(gold, dict) or not isinstance(gold.get('items'), list):
        raise InputError('invalid_gold')
    expected = {}
    for row in gold['items']:
        if (not isinstance(row, dict) or not _nonempty(row.get('id'))
                or row.get('expected') not in DECISIONS
                or row['id'] in expected):
            raise InputError('invalid_gold')
        expected[row['id']] = row['expected']
    ids = list(index) if selected_ids is None else list(selected_ids)
    if (not ids or any(not _nonempty(item) for item in ids)
            or len(ids) != len(set(ids))
            or any(item not in index or item not in expected for item in ids)):
        raise InputError('invalid_selected_ids')
    return {item: index[item] for item in ids}, {item: expected[item] for item in ids}


def _row_errors(row, example):
    errors = []
    if set(row) != FIELDS:
        errors.append('fields')
    if row.get('decision') not in DECISIONS:
        errors.append('decision')
    if not _nonempty(row.get('reason')):
        errors.append('reason')
    evidence = row.get('evidence')
    if not isinstance(evidence, list):
        errors.append('evidence_type')
    else:
        if row.get('decision') == 'accept' and not evidence:
            errors.append('accept_requires_evidence')
        if any(not _has_quote(quote, example) for quote in evidence):
            errors.append('nonverbatim_evidence')
    if 'russia_basis' not in row:
        errors.append('missing_russia_basis')
    else:
        basis = row['russia_basis']
        if basis is not None and not _has_quote(basis, example):
            errors.append('nonverbatim_russia_basis')
    return errors


def evaluate_response(raw, examples, gold, *, selected_ids=None,
                      duration_seconds=None):
    """Return formal metrics. Quote meaning and legal claims need human review."""
    index, expected = prepare_inputs(examples, gold, selected_ids)
    if duration_seconds is not None and (
            isinstance(duration_seconds, bool)
            or not isinstance(duration_seconds, (int, float))
            or not math.isfinite(duration_seconds) or duration_seconds < 0):
        raise InputError('invalid_duration')
    packet_errors = []
    json_valid = True
    try:
        parsed = decode(raw)
    except (ValueError, TypeError, RecursionError):
        parsed = None
        json_valid = False
        packet_errors.append('invalid_json')
    if json_valid and not isinstance(parsed, list):
        packet_errors.append('expected_array')
    rows = {}
    visible_unsafe = set()
    if isinstance(parsed, list):
        ids = []
        for row in parsed:
            if not isinstance(row, dict) or not _nonempty(row.get('id')):
                packet_errors.append('invalid_item_id')
                continue
            item = row['id']
            ids.append(item)
            if item in index:
                rows.setdefault(item, row)
                if row.get('decision') == 'accept' and expected[item] != 'accept':
                    visible_unsafe.add(item)
        counts = Counter(ids)
        if any(count > 1 for count in counts.values()):
            packet_errors.append('duplicate_id')
        if set(ids) - set(index):
            packet_errors.append('unexpected_id')
        if set(index) - set(ids):
            packet_errors.append('missing_id')
    packet_errors = list(dict.fromkeys(packet_errors))
    results = []
    confusion = {item: {label: 0 for label in (*DECISIONS, 'invalid')}
                 for item in DECISIONS}
    for item, example in index.items():
        row = rows.get(item)
        errors = list(packet_errors)
        if row is None:
            if 'missing_id' not in errors:
                errors.append('missing_id')
        else:
            errors.extend(_row_errors(row, example))
        decision = row.get('decision') if row else None
        if decision not in DECISIONS:
            decision = None
        valid = not errors
        actual = expected[item]
        confusion[actual][decision if valid else 'invalid'] += 1
        results.append(dict(id=item, expected=actual, decision=decision,
                            valid=valid, errors=list(dict.fromkeys(errors)),
                            correct=valid and decision == actual))
    valid_count = sum(row['valid'] for row in results)
    return dict(
        ids=list(index), json_valid=json_valid, packet_errors=packet_errors,
        total=len(index), valid=valid_count, invalid=len(index) - valid_count,
        comparison_eligible=valid_count == len(index),
        metrics_status='complete' if valid_count == len(index) else 'incomplete_not_for_comparison',
        correct=sum(row['correct'] for row in results),
        # Include duplicate rows, so a later unsafe decision cannot be hidden.
        unsafe_accepts=len(visible_unsafe),
        unsafe_accepts_basis='observed_known_ids_including_invalid_rows',
        missed_accepts=sum(row['expected'] == 'accept'
                          and not (row['valid'] and row['decision'] == 'accept')
                          for row in results),
        unnecessary_review=sum(row['valid'] and row['decision'] == 'review' and row['expected'] == 'reject'
                               for row in results),
        confusion=confusion, items=results, duration_seconds=duration_seconds,
        duration_status='not_measured' if duration_seconds is None else 'measured',
        semantic_review='required_separately',
    )


def _read_json(path):
    try:
        return decode(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise InputError('invalid_input_file') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', required=True, type=Path)
    parser.add_argument('--gold', required=True, type=Path)
    parser.add_argument('--ids', help='Comma-separated, fixed subset of example IDs')
    parser.add_argument('--response', required=True, action='append', nargs=2,
                        metavar=('LABEL', 'FILE'))
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        examples, gold = _read_json(args.inputs), _read_json(args.gold)
        selected = args.ids.split(',') if args.ids is not None else None
        labels = [label for label, _path in args.response]
        if any(not _nonempty(label) for label in labels) or len(labels) != len(set(labels)):
            raise InputError('invalid_labels')
        results = {}
        for label, path in args.response:
            try:
                raw = Path(path).read_text(encoding='utf-8-sig')
            except (OSError, UnicodeError):
                raise InputError('unreadable_response_file') from None
            results[label] = evaluate_response(raw, examples, gold, selected_ids=selected)
        output = json.dumps(dict(results=results, model_api_calls=0,
                                 note='Formal checks only; no automatic model selection.'),
                            ensure_ascii=False, indent=2) + '\n'
        if args.output:
            try:
                args.output.write_text(output, encoding='utf-8')
            except OSError:
                raise InputError('unwritable_output_file') from None
        else:
            print(output, end='')
        return 0 if all(row['invalid'] == 0 for row in results.values()) else 1
    except InputError as error:
        parser.error(str(error))


if __name__ == '__main__':
    raise SystemExit(main())
