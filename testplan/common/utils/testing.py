import sys
import functools
import logging
import pprint
import os
import warnings

import six
from lxml import objectify

from contextlib import contextmanager
from ..report.base import Report, ReportGroup
from ..utils.comparison import is_regex


null_handler = logging.NullHandler()


def context_wrapper(ctx_manager, *ctx_args, **ctx_kwargs):
    """
    Higher order function that returns a decorator that runs the wrapped
    func within the context of the given `ctx_manager` initialized
    by `ctx_args` and `ctx_kwargs`
    """
    def _wrapper(func):
        @functools.wraps(func)
        def _inner(*args, **kwargs):
            with ctx_manager(*ctx_args, **ctx_kwargs):
                return func(*args, **kwargs)
        return _inner
    return _wrapper


@contextmanager
def argv_overridden(*override_ctx):
    """
    Override sys.argv for the given context.
    This is not a thread safe operation, may cause issues
    if you run tests in parallel threads.
    """
    argv_backup = list(sys.argv)
    sys.argv = [argv_backup[0]] + list(override_ctx)
    yield
    sys.argv = argv_backup


def override_argv(*override_ctx):
    """Override sys.argv for the wrapped function."""
    return context_wrapper(argv_overridden, *override_ctx)


@contextmanager
def log_propagation_disabled(logger):
    """
    Disables log propagation for the given logger.

    WARNING: Use this logic sparingly as it will hide
    actual exceptions from getting displayed in the console.

    A use case would be testing out exception logging to
    a custom target without showing these messages in
    the console itself, leaving us with a clean console output.
    """
    old_prop = logger.propagate
    logger.propagate = False

    logger.addHandler(null_handler)  # This prevents No handler found warning.
    yield
    logger.propagate = old_prop
    logger.removeHandler(null_handler)


def disable_log_propagation(logger):
    """Disables log propagation for the given logger."""
    return context_wrapper(log_propagation_disabled, logger)


@contextmanager
def captured_logging(logger):
    """
    Utility for capturing a logger object's output.
    Useful for command line output testing.
    """

    class LogWrapper(object):
        def __init__(self):
            self.buffer = six.StringIO()
            self.stream_handler = logging.StreamHandler(self.buffer)
            self._output = None

        @property
        def output(self):
            if self._output is None:
                self.stream_handler.flush()
                # Standardize line endings
                self._output = self.buffer\
                    .getvalue().replace('\r\n', '\n')
            return self._output

    log_wrapper = LogWrapper()
    logger.addHandler(log_wrapper.stream_handler)
    yield log_wrapper
    logger.removeHandler(log_wrapper.stream_handler)


def to_stdout(*items):
    """
    Utility function that can be used for testing
    logging output along with `captured_logging`.
    """
    return '\n'.join(items) + '\n'


@contextmanager
def warnings_suppressed():
    """Suppress warnings within a block"""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield


def suppress_warnings(func):
    """Suppress warnings within a function"""
    return context_wrapper(warnings_suppressed)


def check_iterable(expected, actual, curr_path='ROOT', _orig_exp=None, _orig_act=None):
    """
    Utility for checking an iterable, supports custom
    func assertions along with normal value matches.
    """
    _orig_act = _orig_act or actual
    _orig_exp = _orig_exp or expected

    def render_mismatch(act, exp, full_path):
        return (
            '{linesep}'
            'Mismatch: "{full_path}",{linesep}'
            'Expected value: {expected}{linesep}'
            'Actual value: {actual}{linesep}'
            'Expected Data:{linesep}{exp_data}{linesep}'
            'Actual Data:{linesep}{act_data}'
        ).format(
            full_path=full_path,
            linesep=os.linesep,
            expected=pprint.pformat(exp, indent=2),
            actual=pprint.pformat(act, indent=2),
            exp_data=pprint.pformat(_orig_exp, indent=2),
            act_data=pprint.pformat(_orig_act, indent=2)
        )

    msg = render_mismatch(
        act=actual, exp=expected, full_path=curr_path)

    if isinstance(expected, (list, tuple)):
        for idx, (exp_item, act_item) in enumerate(zip(expected, actual)):
            check_iterable(
                expected=exp_item,
                actual=act_item,
                curr_path='{} | [{}]'.format(curr_path, idx),
                _orig_exp=_orig_exp,
                _orig_act=_orig_act,
            )
    elif isinstance(expected, dict):
        for key, value in expected.items():
            check_iterable(
                actual=actual[key],
                expected=value,
                curr_path='{} | {}'.format(curr_path, key),
                _orig_exp=_orig_exp,
                _orig_act=_orig_act,
            )
    elif callable(expected):
        assert bool(expected(actual)), msg
    elif is_regex(expected):
        msg = render_mismatch(
            act=actual,
            exp=expected.pattern,
            full_path=curr_path
        )
        assert expected.search(actual), msg
    else:
        assert expected == actual, msg


def check_entry(expected, actual):
    """Utility function for comparing serialized entries."""
    if expected['type'] == 'Group':
        assert len(expected['entries']) == len(actual['entries'])
        for expected_child, actual_child in zip(
                expected['entries'], actual['entries']):
            check_entry(
                expected_child, actual_child)
    else:
        check_iterable(expected, actual, _orig_act=actual, _orig_exp=expected)


def check_report(expected, actual, skip=None):
    """
    Utility function for comparing report objects.

    Skip uid attribute, entries will be
    checked recursively via `check_entry`.
    """
    skip = skip or []
    attrs = [
        attr for attr in expected._get_comparison_attrs()
        if attr not in ['entries', 'uid', 'timer'] + skip
    ]

    for attr in attrs:
        exp_value = getattr(expected, attr)
        act_value = getattr(actual, attr)

        if isinstance(act_value, (list, dict, tuple)):
            check_iterable(exp_value, act_value)
        else:
            msg = 'Mismatch: "{}", `{}` != `{}`'.format(
                attr, exp_value, act_value)
            assert exp_value == act_value, msg

    if isinstance(expected, ReportGroup):
        msg = '{} {} {}'.format(
            pprint.pformat(expected, indent=2),
            os.linesep,
            pprint.pformat(actual, indent=2)
        )
        assert len(expected) == len(actual), msg
        for expected_child, actual_child in zip(
                expected.entries, actual.entries):
            check_report(expected_child, actual_child, skip=skip)

    elif isinstance(expected, Report):
        assert len(expected) == len(actual)
        for expected_entry, actual_entry in zip(
                expected.entries, actual.entries):
            check_entry(expected_entry, actual_entry)


def check_report_context(report, ctx):
    """
    Utility function for checking filtered/ordered test results, we are not
    interested in report contents, just the existence of reports
    with matching names, with the correct order.
    """
    for mt_report, (multitest_name, suite_ctx) in zip(report, ctx):
        assert mt_report.name == multitest_name
        assert len(mt_report) == len(suite_ctx)

        for suite_report, (suite_name, testcases) in zip(mt_report, suite_ctx):
            assert suite_report.name == suite_name
            assert len(suite_report) == len(testcases), '{}, {}'.format(
                suite_report.entries, testcases)

            for testcase_report, testcase_name in zip(suite_report, testcases):
                assert testcase_report.name == testcase_name


def py_version_data(py2, py3):
    """
    Return the related data for the given python version.
    This is mostly used for comparing randomly generated
    values as random produces inconsistent results
    between python 2 and 3.
    """
    if six.PY2:
        return py2
    return py3


class XMLComparison(object):
    """
    Testing utility for generated XML file contents.

    Recursively compares children as well,
    supports simple string or regex matching.

    Usage:

    my_file.xml

    .. code-block:: xml

        <root foo="bar">
            <parent id="0"/>
            <parent id="1">
                <child hello="world" time="12:00"/>
            </parent>
        </root>

    .. code-block:: python

        comparison = XMLComparison(
            tag='root',
            foo='bar',
            children=[
                XMLComparison(tag='parent', id='0'),
                XMLComparison(
                    tag='parent', id='1'
                    children=[
                        XMLComparison(
                            tag='child', hello='world',
                            time=re.compile('\d{2}:\d{2}')
                        )
                    ]
                ),

            ]
        )

        with open('my_file.xml') as xml_file:
            comparison.compare(xml_file)
    """

    def __init__(self, tag, children=None, **kwargs):
        self.tag = tag
        self.children = children or []
        self.attrib = kwargs

    def _compare_value(self, first, second, key):
        msg='Attrib mismatch key: `{key}`, ' \
            'expected: `{expected}`, actual: `{actual}`'

        if is_regex(first):
            assert first.search(second), msg.format(
                key=key, expected=first.pattern, actual=second)
        else:
            assert first == second, msg.format(
                key=key, expected=first, actual=second)

    def _compare_obj(self, xml_obj):
        self._compare_value(self.tag, xml_obj.tag, key='tag')
        for key, value in self.attrib.items():
            self._compare_value(value, xml_obj.attrib[key], key)

        xml_children = xml_obj.getchildren()
        num_children = len(self.children)
        num_xml_children = len(xml_children)

        assert num_children == num_xml_children, \
            'Mismatching number of children ({} vs {})'.format(
                num_children,
                num_xml_children)

        for curr_child, xml_child in zip(self.children, xml_children):
            curr_child._compare_obj(xml_child)

    def compare(self, xml_str):
        """Compare with xml string input."""
        xml_obj = objectify.fromstring(xml_str)
        self._compare_obj(xml_obj)