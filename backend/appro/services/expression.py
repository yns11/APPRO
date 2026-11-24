"""Safe evaluation of the arithmetic expressions typed in the simulation grid.

Accepted: numbers (``1 200``, ``1200.5``, ``1200,5``), ``+ - * /``, unary minus and parentheses.
Anything else (names, calls, powers…) is rejected with ``ValueError``.
"""
from __future__ import annotations

import ast
import operator

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
_UN = {ast.USub: operator.neg, ast.UAdd: operator.pos}


def normalise(expr: str) -> str:
    """``" 1 200,5 + 3×2 "`` → ``"1200.5+3*2"`` (spaces removed, French decimals, × and ÷)."""
    s = expr.strip().replace("\u202f", "").replace("\xa0", "").replace(" ", "")
    s = s.replace("×", "*").replace("÷", "/").replace("−", "-")
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    return s.lstrip("=")


def evaluate(expr: str) -> float:
    """Return the value of ``expr`` (see module docstring) or raise ``ValueError``."""
    src = normalise(expr)
    if not src:
        raise ValueError("expression vide")
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"expression invalide : {expr!r}") from exc

    def walk(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            right = walk(node.right)
            if isinstance(node.op, ast.Div) and right == 0:
                raise ValueError("division par zéro")
            return _BIN[type(node.op)](walk(node.left), right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UN:
            return _UN[type(node.op)](walk(node.operand))
        raise ValueError(f"expression non autorisée : {expr!r} (nombres, + - * / et parenthèses seulement)")

    value = walk(tree)
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError("résultat non fini")
    return float(value)
