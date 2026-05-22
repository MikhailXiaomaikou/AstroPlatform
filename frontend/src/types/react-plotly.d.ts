/* eslint-disable @typescript-eslint/no-explicit-any */
// Q3: Third-party Plotly type shim. The official @types/react-plotly.js is large and
// often out of sync with minor versions of react-plotly.js. This d.ts only exposes
// the 5 fields the app actually uses; full Plotly type fidelity is not required, so any is an acceptable trade-off.
declare module "react-plotly.js" {
  import { Component } from "react";
  interface PlotParams {
    data: any[];
    layout?: Record<string, any>;
    config?: Record<string, any>;
    style?: React.CSSProperties;
    className?: string;
    onHover?: (event: any) => void;
    onClick?: (event: any) => void;
  }
  export default class Plot extends Component<PlotParams> {}
}
