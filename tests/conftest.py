from dataclasses import asdict

import pytest

from cf_challenge.models import Prepared, Problem, Round, Statement


@pytest.fixture
def problem():
    return Problem(1000, "A", "Hidden Identity", 1200, ("dp", "math"))


@pytest.fixture
def source():
    return Statement(
        body="Given $$$n$$$ integers, find their sum.",
        input_spec="First $$$n$$$, then the integers. $$$1 \\le n \\le 10^5$$$.",
        output_spec="Print the sum.",
        samples=(("3\n1 2 3", "6"),),
        time_limit="1 second",
        memory_limit="256 megabytes",
    )


@pytest.fixture
def prepared():
    return Prepared(
        "给出 $$$n$$$ 个整数，求它们的和。",
        "先输入 $$$n$$$，再输入这些整数。$$$1 \\le n \\le 10^5$$$。",
        "输出总和。",
        "",
        "PRIVATE_REFERENCE：逐个累加，O(n) 时间。",
    )


@pytest.fixture
def round_(problem, source, prepared):
    return Round(
        "abcdef123456", "qq:group:123", problem, source, prepared, status="active"
    )


@pytest.fixture
def translated(prepared):
    return {k: v for k, v in asdict(prepared).items() if k != "reference"}
