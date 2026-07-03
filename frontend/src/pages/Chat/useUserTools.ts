// User-tools sidebar state + composer prefill for saved tools.
// State, effect, and handlers moved verbatim from ChatPage.tsx.
import { useState, useEffect, useCallback, type RefObject } from "react";
import { listUserTools, type UserToolDefinition } from "../../api/userTools";
import type { UserProfile } from "../../api/client";
import { exampleArgsForUserTool, type ToastState } from "./chatHelpers";

export function useUserTools({
  user,
  setInput,
  showToast,
  inputRef,
}: {
  user: UserProfile | null;
  setInput: (value: string) => void;
  showToast: (message: string, tone?: ToastState["tone"]) => void;
  inputRef: RefObject<HTMLTextAreaElement | null>;
}) {
  const [userTools, setUserTools] = useState<UserToolDefinition[]>([]);
  const [userToolsLoading, setUserToolsLoading] = useState(false);
  const [userToolsError, setUserToolsError] = useState<string | null>(null);

  const refreshUserTools = useCallback(async () => {
    if (!user) {
      setUserTools([]);
      setUserToolsError(null);
      setUserToolsLoading(false);
      return;
    }
    setUserToolsLoading(true);
    setUserToolsError(null);
    try {
      setUserTools(await listUserTools());
    } catch (err) {
      setUserTools([]);
      setUserToolsError(err instanceof Error ? err.message : String(err));
    } finally {
      setUserToolsLoading(false);
    }
  }, [user]);

  useEffect(() => {
    void refreshUserTools();
  }, [refreshUserTools]);

  const handleUseUserTool = useCallback((tool: UserToolDefinition) => {
    const args = exampleArgsForUserTool(tool);
    setInput(
      `Run my saved user tool \`${tool.tool_id}\` with arguments:\n\n${JSON.stringify(args, null, 2)}`,
    );
    showToast(`Prepared ${tool.display_name || tool.tool_id} in the composer.`, "info");
    window.setTimeout(() => inputRef.current?.focus(), 0);
    // setInput is ChatPage's useState setter and inputRef a useRef (both stable identities); dep array preserved verbatim from the pre-split code.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showToast]);

  return {
    userTools,
    userToolsLoading,
    userToolsError,
    refreshUserTools,
    handleUseUserTool,
  };
}
