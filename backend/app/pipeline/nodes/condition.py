"""Condition node — evaluates an expression on upstream data and routes to true/false branches."""


def condition(input_data: dict, params: dict) -> dict:
    """Evaluate a condition on upstream data and route to true/false branches.

    params:
        expression: Python expression to evaluate (e.g., "row_count > 100")

    The expression can reference keys from input_data directly.
    Evaluated in a restricted namespace with only: len, min, max, sum, abs, int, float, str, bool.
    """
    expression = params.get("expression", "True")

    # Build safe namespace from input_data keys
    namespace = {
        "len": len, "min": min, "max": max, "sum": sum,
        "abs": abs, "int": int, "float": float, "str": str, "bool": bool,
    }
    if isinstance(input_data, dict):
        namespace.update(input_data)

    result = bool(eval(expression, {"__builtins__": {}}, namespace))

    return {
        **input_data,
        "_condition_result": result,
        "_condition_expression": expression,
        "_branch": "true" if result else "false",
    }
