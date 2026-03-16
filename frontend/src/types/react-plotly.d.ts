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
