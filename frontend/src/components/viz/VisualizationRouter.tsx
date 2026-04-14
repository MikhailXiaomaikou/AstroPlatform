import { lazy, Suspense } from "react";

const SpectrumViewer = lazy(() => import("./SpectrumViewer"));
const LightCurveViewer = lazy(() => import("./LightCurveViewer"));
const MCMCDiagnostics = lazy(() => import("./MCMCDiagnostics"));
const ImageCutoutViewer = lazy(() => import("./ImageCutoutViewer"));

interface VisualizationRouterProps {
  data: Record<string, unknown>;
}

export default function VisualizationRouter({ data }: VisualizationRouterProps) {
  const nodeType = data.node_type as string || "";

  // Spectrum data
  if (data.wavelength && data.flux && (nodeType.includes("Spectral") || nodeType === "FluxCalibrate"
      || nodeType === "TelluricCorrect" || nodeType === "SpectraStack" || nodeType === "EquivalentWidth")) {
    return (
      <Suspense fallback={<div>Loading viewer...</div>}>
        <SpectrumViewer
          wavelength={data.wavelength as number[]}
          flux={data.flux as number[]}
          fluxErr={data.flux_err as number[] | undefined}
          lines={data.identified_lines as { observed_wavelength: number; identification?: string; line_type?: string }[] | undefined}
          redshift={(data.redshift as number) || 0}
          title={nodeType}
        />
      </Suspense>
    );
  }

  // Light curve / time series
  if (data.time && data.flux && (nodeType.includes("Transit") || nodeType === "GPDetrend"
      || nodeType === "TimeSeriesAnalysis")) {
    return (
      <Suspense fallback={<div>Loading viewer...</div>}>
        <LightCurveViewer
          time={data.time as number[]}
          flux={(data.flux_detrended || data.flux) as number[]}
          modelFlux={data.model_flux as number[] | undefined}
          trend={data.trend as number[] | undefined}
          flares={data.flares as { start_time: number; end_time: number; peak_time: number }[] | undefined}
          period={data.fitted_params ? (data.fitted_params as Record<string, number>).period : undefined}
          title={nodeType}
        />
      </Suspense>
    );
  }

  // MCMC / Bayesian results
  if (data.diagnostics || data.parameter_summary) {
    return (
      <Suspense fallback={<div>Loading viewer...</div>}>
        <MCMCDiagnostics
          diagnostics={data.diagnostics as { parameters?: Record<string, { rhat: number; ess: number; mcse: number; status: string }>; overall_status?: string } | undefined}
          parameterSummary={data.parameter_summary as Record<string, { median: number; std: number; q16: number; q84: number }> | undefined}
        />
      </Suspense>
    );
  }

  // Image data
  if (data.image_data || nodeType === "Deblend" || nodeType === "Reproject" || nodeType === "PSFMatch") {
    return (
      <Suspense fallback={<div>Loading viewer...</div>}>
        <ImageCutoutViewer
          imageData={data.image_data as number[][] | undefined}
          sources={data.sources as { x: number; y: number; id: number }[] | undefined}
          ra={data.center_ra as number | undefined}
          dec={data.center_dec as number | undefined}
          title={nodeType}
        />
      </Suspense>
    );
  }

  return null;
}
