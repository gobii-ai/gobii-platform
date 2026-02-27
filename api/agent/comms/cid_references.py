import re


CID_SRC_REFERENCE_RE = re.compile(
    r"""(?P<prefix>\bsrc\s*=\s*)(?:"(?P<dq>cid:[^"]+)"|'(?P<sq>cid:[^']+)'|(?P<bare>cid:[^\s>]+))""",
    re.IGNORECASE,
)
