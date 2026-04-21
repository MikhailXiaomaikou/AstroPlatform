/* eslint-disable @typescript-eslint/no-explicit-any */
// Q3: 第三方 Plotly 类型 shim. 官方 @types/react-plotly.js 庞大 + 经常
// 跟 react-plotly.js 小版本不同步. 本 d.ts 只暴露 app 真正用到的 5 个
// 字段, 不强求 Plotly 完整类型保真, any 是合理 trade-off.
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
