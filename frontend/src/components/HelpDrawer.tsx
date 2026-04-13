import { useState } from "react";
import HelpPage from "../pages/Help/HelpPage";

export default function HelpDrawer() {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        className="help-drawer-trigger"
        onClick={() => setOpen(true)}
        aria-label="Help"
        title="Help & Documentation"
      >
        ?
      </button>
      {open && (
        <div className="help-drawer-backdrop" onClick={() => setOpen(false)}>
          <div className="help-drawer" onClick={(e) => e.stopPropagation()}>
            <div className="help-drawer-header">
              <h2>Help &amp; Documentation</h2>
              <button onClick={() => setOpen(false)} aria-label="Close" className="help-drawer-close">&times;</button>
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
