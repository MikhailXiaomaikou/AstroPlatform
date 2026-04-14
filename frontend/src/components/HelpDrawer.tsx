import { useState } from "react";
import HelpPage from "../pages/Help/HelpPage";
import { useI18n } from "../i18n";

export default function HelpDrawer() {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        className="help-drawer-trigger"
        onClick={() => setOpen(true)}
        aria-label={t("help.button_label")}
        title={t("help.drawer_title")}
      >
        ?
      </button>
      {open && (
        <div className="help-drawer-backdrop" onClick={() => setOpen(false)}>
          <div className="help-drawer" onClick={(e) => e.stopPropagation()}>
            <div className="help-drawer-header">
              <h2>{t("help.drawer_title")}</h2>
              <button onClick={() => setOpen(false)} aria-label={t("common.close")} className="help-drawer-close">&times;</button>
            </div>
            <div className="help-drawer-body">
              <HelpPage />
            </div>
          </div>
        </div>
      )}
    </>
  );
}
