import { describe, expect, it } from "vitest";
import { apiErrorCode, localizedApiError } from "../utils/apiErrors";

describe("Foundry stable API error localization", () => {
  it("prefers a localized stable code over mutable backend prose", () => {
    const error = {
      response: {
        data: {
          detail: {
            code: "immutable_version_mismatch",
            message: "mutable server wording",
          },
        },
      },
    };
    const translate = (key: string) => key === "foundry.error_code.immutable_version_mismatch"
      ? "Refresh the immutable candidate version."
      : key;

    expect(apiErrorCode(error)).toBe("immutable_version_mismatch");
    expect(localizedApiError(error, translate, "foundry.error.action"))
      .toBe("Refresh the immutable candidate version.");
  });

  it("falls back to a translated generic message when no detail is present", () => {
    const translate = (key: string) => key === "foundry.error.load" ? "Localized load error" : key;
    expect(localizedApiError({}, translate, "foundry.error.load")).toBe("Localized load error");
  });
});
