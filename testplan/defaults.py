"""All default values that will be shared between config objects go here."""
import os
from testplan.report.testing.styles import StyleArg

SUMMARY_NUM_PASSING = 5
SUMMARY_NUM_FAILING = 5


# Make sure these values match the defaults in the parser.py,
# otherwise we may end up with inconsistent behaviour re. defaults
# between cmdline and programmatic calls.
PDF_STYLE = StyleArg.SUMMARY.value
STDOUT_STYLE = StyleArg.EXTENDED_SUMMARY.value

REPORT_DIR = os.getcwd()
XML_DIR = os.path.join(REPORT_DIR, 'xml')
PDF_PATH = os.path.join(REPORT_DIR, 'report.pdf')
JSON_PATH = os.path.join(REPORT_DIR, 'report.json')