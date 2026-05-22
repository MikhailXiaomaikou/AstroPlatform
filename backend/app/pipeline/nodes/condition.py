"""Condition node — evaluates an expression on upstream data and routes to true/false branches."""

from asteval import Interpreter


def condition(input_data: dict, params: dict) -> dict:
    """Evaluate a condition on upstream data and route to true/false branches.

    params:
        expression: Python expression to evaluate (e.g., "row_count > 100")

    The expression can reference keys from input_data directly.
    Uses asteval (AST allow-list) instead of bare eval -- bare eval can escape
    to arbitrary code execution via ().__class__.__mro__[1].__subclasses__()
    even when __builtins__ is disabled. asteval by default only permits
    literals, arithmetic, comparisons, and a whitelist of math functions.
    """
    expression = params.get("expression", "True")

    # Inject input_data into the symbol table so expressions can reference upstream keys directly
    symtable = {}
    if isinstance(input_data, dict):
        symtable.update(input_data)

    aeval = Interpreter(
        symtable=symtable,
        minimal=True,           # disable most advanced features (import / def / class / comprehensions, etc.)
        no_print=True,
        no_assert=True,
        no_delete=True,
        no_raise=True,
    )
    value = aeval(expression, show_errors=False)
    if aeval.error:
        # asteval collects a list of errors -- expose the first one to the caller
        err = aeval.error[0].get_error()
        raise ValueError(f"Condition expression error: {err[0]}: {err[1]}")
    result = bool(value)

    return {
        **input_data,
        "_condition_result": result,
        "_condition_expression": expression,
        "_branch": "true" if result else "false",
    }
