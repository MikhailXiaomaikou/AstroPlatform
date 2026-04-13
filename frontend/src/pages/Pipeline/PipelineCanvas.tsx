import { useCallback, useEffect, useRef, useState } from "react";
import ReactFlow, {
  addEdge,
  Background,
  BackgroundVariant,
  ConnectionLineType,
  Controls,
  MiniMap,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
  type ReactFlowInstance,
} from "reactflow";
import "reactflow/dist/style.css";

import PipelineNode from "../../components/nodes/PipelineNode";
import NodePalette from "../../components/nodes/NodePalette";
import NodeParamsEditor from "../../components/nodes/NodeParamsEditor";
import {
  connectPipelineWS,
  createSchedule,
  deleteSchedule,
  exportRunCSV,
  exportRunPDF,
  exportRunVOTable,
  getWorkspace,
  getNodeResult,
  getNodeTypes,
  getTemplates,
  getTemplateDiff,
  getTemplateVersions,
  listSchedules,
  runPipeline,
  getPipelineRun,
  saveTemplateVersion,
  toggleSchedule,
  type DagDiffResult,
  type NodeType,
  type PipelineTemplate,
  type ScheduleItem,
  type VersionSummary,
} from "../../api/client";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { mergeWorkspaceFiles, readWorkspaceCache, type WorkspaceCacheFile } from "../../utils/workspaceCache";

const nodeTypes = { pipeline: PipelineNode };

let idCounter = Date.now();
const nextId = () => `node_${++idCounter}`;

interface NodeProgress {
  status: "pending" | "running" | "completed" | "error";
  error?: string;
}

export default function PipelineCanvas() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const reactFlowWrapper = useRef<HTMLDivElement>(null);
  const [rfInstance, setRfInstance] = useState<ReactFlowInstance | null>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  // Undo/redo history
  const historyRef = useRef<Array<{ nodes: Node[]; edges: Edge[] }>>([]);
  const historyIdxRef = useRef(-1);
  const skipHistoryRef = useRef(false);

  const pushHistory = useCallback(() => {
    if (skipHistoryRef.current) return;
    const snap = { nodes: JSON.parse(JSON.stringify(nodes)), edges: JSON.parse(JSON.stringify(edges)) };
    historyRef.current = historyRef.current.slice(0, historyIdxRef.current + 1);
    historyRef.current.push(snap);
    if (historyRef.current.length > 30) historyRef.current.shift();
    historyIdxRef.current = historyRef.current.length - 1;
  }, [nodes, edges]);

  const undo = useCallback(() => {
    if (historyIdxRef.current <= 0) return;
    historyIdxRef.current--;
    const snap = historyRef.current[historyIdxRef.current];
    skipHistoryRef.current = true;
    setNodes(snap.nodes);
    setEdges(snap.edges);
    setTimeout(() => { skipHistoryRef.current = false; }, 100);
  }, [setNodes, setEdges]);

  const redo = useCallback(() => {
    if (historyIdxRef.current >= historyRef.current.length - 1) return;
    historyIdxRef.current++;
    const snap = historyRef.current[historyIdxRef.current];
    skipHistoryRef.current = true;
    setNodes(snap.nodes);
    setEdges(snap.edges);
    setTimeout(() => { skipHistoryRef.current = false; }, 100);
  }, [setNodes, setEdges]);

  // Keyboard shortcuts for undo/redo
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "z" && !e.shiftKey) { e.preventDefault(); undo(); }
      if ((e.metaKey || e.ctrlKey) && (e.key === "y" || (e.key === "z" && e.shiftKey))) { e.preventDefault(); redo(); }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [undo, redo]);

  const [availableTypes, setAvailableTypes] = useState<NodeType[]>([]);
  const [workspacePaths, setWorkspacePaths] = useState<string[]>([]);
  const [templates, setTemplates] = useState<PipelineTemplate[]>([]);
  const [runStatus, setRunStatus] = useState<string | null>(null);
  const [runResults, setRunResults] = useState<Record<string, unknown> | null>(null);
  const [inputDataId, setInputDataId] = useState("example/fits/path.fits");
  const [nodeProgress, setNodeProgress] = useState<Record<string, NodeProgress>>({});
  const wsRef = useRef<WebSocket | null>(null);
  const [schedules, setSchedules] = useState<ScheduleItem[]>([]);
  const [showScheduler, setShowScheduler] = useState(false);
  const [scheduleName, setScheduleName] = useState("");
  const [scheduleCron, setScheduleCron] = useState("daily");
  const [lastRunId, setLastRunId] = useState<string | null>(null);

  // Version management state
  const [activeTemplateId, setActiveTemplateId] = useState<string | null>(null);
  const [showVersionHistory, setShowVersionHistory] = useState(false);
  const [versions, setVersions] = useState<VersionSummary[]>([]);
  const [compareMode, setCompareMode] = useState(false);
  const [compareSelection, setCompareSelection] = useState<string[]>([]);
  const [diffResult, setDiffResult] = useState<DagDiffResult | null>(null);

  // Node params editor state
  const [editingNodeId, setEditingNodeId] = useState<string | null>(null);

  // Node result inspection panel
  const [inspectResult, setInspectResult] = useState<{ nodeId: string; data: Record<string, unknown> } | null>(null);

  const selectedCount =
    nodes.filter((n) => n.selected).length +
    edges.filter((e) => e.selected).length;

  const handleDelete = useCallback(() => {
    setNodes((nds) => nds.filter((n) => !n.selected));
    setEdges((eds) => {
      const deletedNodeIds = new Set(
        nodes.filter((n) => n.selected).map((n) => n.id)
      );
      return eds.filter(
        (e) =>
          !e.selected &&
          !deletedNodeIds.has(e.source) &&
          !deletedNodeIds.has(e.target)
      );
    });
  }, [nodes, setNodes, setEdges]);

  const handleClearAll = useCallback(() => {
    setNodes([]);
    setEdges([]);
    setNodeProgress({});
    localStorage.removeItem("pipeline_autosave");
  }, [setNodes, setEdges]);

  // Node params editor handlers
  const handleNodeDoubleClick = useCallback((_event: React.MouseEvent, node: Node) => {
    setEditingNodeId(node.id);
  }, []);

  // Single-click: if node is completed and we have a run, show its result
  const handleNodeClick = useCallback((_event: React.MouseEvent, node: Node) => {
    const progress = nodeProgress[node.id];
    if (progress?.status === "completed" && lastRunId) {
      getNodeResult(lastRunId, node.id)
        .then((data) => setInspectResult({ nodeId: node.id, data }))
        .catch(() => {
          // Fall back to the full run results if the per-node endpoint fails
          if (runResults && typeof runResults === "object" && node.id in runResults) {
            setInspectResult({ nodeId: node.id, data: runResults[node.id] as Record<string, unknown> });
          }
        });
    } else {
      // Not completed — open params editor instead
      setEditingNodeId(node.id);
    }
  }, [nodeProgress, lastRunId, runResults]);

  const handleParamsApply = useCallback(
    (nodeId: string, params: Record<string, unknown>) => {
      setNodes((nds) =>
        nds.map((n) =>
          n.id === nodeId ? { ...n, data: { ...n.data, params } } : n
        )
      );
      setEditingNodeId(null);
    },
    [setNodes]
  );

  const editingNode = editingNodeId
    ? nodes.find((n) => n.id === editingNodeId) ?? null
    : null;

  // Load node types and templates on mount
  useEffect(() => {
    getNodeTypes()
      .then(setAvailableTypes)
      .catch(() =>
        setAvailableTypes([
          { type: "QueryData", label: "Query Data", description: "Search catalog sources", inputs: 0, outputs: 1 },
          { type: "ImportWorkspace", label: "Import Workspace", description: "Load a Workspace file", inputs: 0, outputs: 1 },
          { type: "LoadData", label: "Load Data", description: "Load FITS file", inputs: 0, outputs: 1 },
          { type: "BiasSubtract", label: "Bias Subtract", description: "Subtract a master bias", inputs: 1, outputs: 1 },
          { type: "DarkCorrect", label: "Dark Correct", description: "Subtract a scaled master dark", inputs: 1, outputs: 1 },
          { type: "FlatField", label: "Flat Field", description: "Divide by a normalized master flat", inputs: 1, outputs: 1 },
          { type: "CosmicRayReject", label: "Cosmic Ray Reject", description: "Clean cosmic rays", inputs: 1, outputs: 2 },
          { type: "AstrometricSolve", label: "Astrometric Solve", description: "Solve WCS metadata", inputs: 1, outputs: 1 },
          { type: "SourceExtract", label: "Source Extract", description: "Run source extraction and photometry", inputs: 1, outputs: 1 },
          { type: "Denoise", label: "Denoise", description: "Sigma-clip noise", inputs: 1, outputs: 1 },
          { type: "SpectralFit", label: "Spectral Fit", description: "Fit spectral line", inputs: 1, outputs: 1 },
          { type: "CoordTransform", label: "Coord Transform", description: "Transform coordinates", inputs: 1, outputs: 1 },
          { type: "Plot", label: "Plot", description: "Generate plot", inputs: 1, outputs: 1 },
          { type: "RedshiftEstimate", label: "Redshift", description: "Estimate redshift from spectral lines", inputs: 1, outputs: 1 },
          { type: "EquivalentWidth", label: "Equiv. Width", description: "Measure spectral line equivalent width", inputs: 1, outputs: 1 },
          { type: "SEDFit", label: "SED Fit", description: "Fit spectral energy distribution", inputs: 1, outputs: 1 },
          { type: "CrossMatch", label: "Cross-Match", description: "Cross-match two catalogs", inputs: 1, outputs: 1 },
          { type: "PhotCalibrate", label: "Phot. Calibrate", description: "Apply photometric calibration", inputs: 1, outputs: 1 },
          { type: "ImageStack", label: "Image Stack", description: "Stack/combine images", inputs: 1, outputs: 1 },
          { type: "InteractivePlot", label: "Interactive Plot", description: "Generate interactive Plotly chart", inputs: 1, outputs: 1 },
          { type: "Condition", label: "Condition", description: "Branch pipeline based on a condition", inputs: 1, outputs: 2 },
        ])
      );
    getTemplates().then(setTemplates).catch(() => {});
    listSchedules().then(setSchedules).catch(() => {});
    getWorkspace()
      .then((files) => {
        const remoteFiles = files as unknown as WorkspaceCacheFile[];
        setWorkspacePaths(
          mergeWorkspaceFiles([...remoteFiles, ...readWorkspaceCache()])
            .map((file) => file.fits_path)
        );
      })
      .catch(() => setWorkspacePaths(readWorkspaceCache().map((file) => file.fits_path)));
  }, []);

  // Autosave pipeline to localStorage
  useEffect(() => {
    if (nodes.length === 0 && edges.length === 0) return;
    try {
      const state = { nodes, edges, inputDataId };
      localStorage.setItem("pipeline_autosave", JSON.stringify(state));
    } catch { /* storage full */ }
  }, [nodes, edges, inputDataId]);

  // Banner shown when pipeline was pre-loaded from Data Browser
  const [importBanner, setImportBanner] = useState<string | null>(null);

  // Autorestore on mount
  useEffect(() => {
    try {
      const saved = localStorage.getItem("pipeline_autosave");
      if (saved) {
        const parsed = JSON.parse(saved);
        const sn = parsed.nodes as Node[] | undefined;
        const se = parsed.edges as Edge[] | undefined;
        const sid = parsed.inputDataId as string | undefined;
        const from = parsed._from as string | undefined;
        if (Array.isArray(sn) && sn.length > 0) {
          // Normalise node type — Data Browser drafts may use the backend
          // node type (e.g. "LoadData") instead of the ReactFlow custom type
          const normalised = sn.map((n) => ({
            ...n,
            type: "pipeline" as const,
          }));
          setNodes(normalised);
          setEdges(se || []);
          if (sid) setInputDataId(sid);

          if (from === "data_browser") {
            // Show a temporary banner & clear the flag so it doesn't repeat
            setImportBanner(`Pipeline pre-loaded with data: ${sid ?? "unknown"}`);
            const cleaned = { ...parsed };
            delete cleaned._from;
            localStorage.setItem("pipeline_autosave", JSON.stringify(cleaned));
            setTimeout(() => setImportBanner(null), 5000);
          }
        }
      }
    } catch { /* ignore corrupt data */ }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Cleanup WebSocket on unmount
  useEffect(() => {
    return () => {
      wsRef.current?.close();
    };
  }, []);

  const onConnect = useCallback(
    (connection: Connection) => {
      pushHistory();
      setEdges((eds) => addEdge({ ...connection, animated: true }, eds));
    },
    [setEdges, pushHistory]
  );

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  }, []);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const type = e.dataTransfer.getData("application/reactflow-type");
      const label = e.dataTransfer.getData("application/reactflow-label");
      if (!type || !rfInstance || !reactFlowWrapper.current) return;

      const bounds = reactFlowWrapper.current.getBoundingClientRect();
      const position = rfInstance.screenToFlowPosition({
        x: e.clientX - bounds.left,
        y: e.clientY - bounds.top,
      });

      const newNode: Node = {
        id: nextId(),
        type: "pipeline",
        position,
        data: { label: label || type, nodeType: type, params: {} },
      };
      setNodes((nds) => [...nds, newNode]);
    },
    [rfInstance, setNodes]
  );

  const loadTemplate = (tpl: PipelineTemplate) => {
    const flowNodes: Node[] = tpl.dag.nodes.map((n) => ({
      id: n.id,
      type: "pipeline",
      position: n.position,
      data: {
        label: (n.data as Record<string, unknown>).label || n.type,
        nodeType: n.type,
        params: (n.data as Record<string, unknown>).params || {},
      },
    }));
    const flowEdges: Edge[] = tpl.dag.edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle: e.sourceHandle,
      animated: true,
    }));
    setNodes(flowNodes);
    setEdges(flowEdges);
    setNodeProgress({});
    setActiveTemplateId(tpl.id);
    setShowVersionHistory(false);
    setDiffResult(null);
    setCompareMode(false);
    setCompareSelection([]);
  };

  const handleSaveVersion = async () => {
    if (!activeTemplateId || nodes.length === 0) return;
    const changeNote = prompt("Enter a change note for this version:");
    if (changeNote === null) return;
    const dag = {
      nodes: nodes.map((n) => ({
        id: n.id,
        type: n.data.nodeType as string,
        position: n.position,
        data: { label: n.data.label, params: n.data.params || {} },
      })),
      edges: edges.map((e) => ({ id: e.id, source: e.source, target: e.target, ...(e.sourceHandle ? { sourceHandle: e.sourceHandle } : {}) })),
    };
    try {
      await saveTemplateVersion(activeTemplateId, dag, changeNote);
      setRunStatus("Version saved");
    } catch {
      setRunStatus("Failed to save version");
    }
  };

  const handleShowHistory = async () => {
    if (!activeTemplateId) return;
    try {
      const vers = await getTemplateVersions(activeTemplateId);
      setVersions(vers);
      setShowVersionHistory(true);
      setCompareMode(false);
      setCompareSelection([]);
      setDiffResult(null);
    } catch {
      setRunStatus("Failed to load version history");
    }
  };

  const handleToggleCompareSelection = (versionId: string) => {
    setCompareSelection((prev) => {
      if (prev.includes(versionId)) {
        return prev.filter((id) => id !== versionId);
      }
      if (prev.length >= 2) {
        return [prev[1], versionId];
      }
      return [...prev, versionId];
    });
  };

  const handleCompare = async () => {
    if (!activeTemplateId || compareSelection.length !== 2) return;
    try {
      const diff = await getTemplateDiff(
        activeTemplateId,
        compareSelection[0],
        compareSelection[1]
      );
      setDiffResult(diff);
    } catch {
      setRunStatus("Failed to compute diff");
    }
  };

  const handleRun = async () => {
    if (nodes.length === 0) return;

    const dag = {
      nodes: nodes.map((n) => ({
        id: n.id,
        type: n.data.nodeType,
        position: n.position,
        data: { label: n.data.label, params: n.data.params || {} },
      })),
      edges: edges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        ...(e.sourceHandle ? { sourceHandle: e.sourceHandle } : {}),
      })),
    };

    // Reset progress
    const initialProgress: Record<string, NodeProgress> = {};
    nodes.forEach((n) => {
      initialProgress[n.id] = { status: "pending" };
    });
    setNodeProgress(initialProgress);

    setRunStatus("submitting...");
    try {
      const res = await runPipeline(dag, inputDataId);

      if (res.status === "running") {
        // Async mode — connect WebSocket for progress
        setRunStatus(`Run ${res.run_id} — running...`);
        wsRef.current?.close();
        wsRef.current = connectPipelineWS(
          res.run_id,
          (data) => {
            const type = data.type as string;
            const nodeId = data.node_id as string;

            if (type === "node_start" && nodeId) {
              setNodeProgress((prev) => ({
                ...prev,
                [nodeId]: { status: "running" },
              }));
            } else if (type === "node_complete" && nodeId) {
              setNodeProgress((prev) => ({
                ...prev,
                [nodeId]: { status: "completed" },
              }));
            } else if (type === "node_error" && nodeId) {
              setNodeProgress((prev) => ({
                ...prev,
                [nodeId]: { status: "error", error: data.error as string },
              }));
            } else if (type === "run_complete") {
              setRunStatus(`Run ${res.run_id} — completed`);
              setLastRunId(res.run_id);
              if (data.results) setRunResults(data.results as Record<string, unknown>);
              getPipelineRun(res.run_id).then((run) => {
                if (run.results) setRunResults(run.results as Record<string, unknown>);
              }).catch(() => {});
              // Mark all remaining as completed
              setNodeProgress((prev) => {
                const updated = { ...prev };
                for (const key of Object.keys(updated)) {
                  if (updated[key].status === "pending" || updated[key].status === "running") {
                    updated[key] = { status: "completed" };
                  }
                }
                return updated;
              });
              wsRef.current?.close();
            }
          },
          () => {
            // WebSocket closed
          }
        );
      } else {
        // Sync mode — all done
        setRunStatus(`Run ${res.run_id} — ${res.status}`);
        setLastRunId(res.run_id);
        if (res.results) {
          setRunResults(res.results as Record<string, unknown>);
        }
        // Fetch full results
        getPipelineRun(res.run_id).then((run: Record<string, unknown>) => {
          if (run.results) {
            setRunResults(run.results as Record<string, unknown>);
            // Set per-node status based on actual results
            const results = run.results as Record<string, Record<string, unknown>>;
            setNodeProgress((prev) => {
              const updated = { ...prev };
              for (const [nid, r] of Object.entries(results)) {
                if (r.error) {
                  updated[nid] = { status: "error", error: String(r.error) };
                } else {
                  updated[nid] = { status: "completed" };
                }
              }
              return updated;
            });
          }
        }).catch(() => {});
        setNodeProgress((prev) => {
          const updated = { ...prev };
          for (const key of Object.keys(updated)) {
            if (updated[key].status === "pending") {
              updated[key] = { status: "completed" };
            }
          }
          return updated;
        });
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Run failed";
      setRunStatus(`Error: ${msg}`);
    }
  };

  const handleOpenAI = useCallback(() => {
    localStorage.setItem(
      "astro_chat_draft",
      "Review my current pipeline, identify weak spots, and suggest the next modification."
    );
    localStorage.setItem("pipeline_autosave", JSON.stringify({ nodes, edges, inputDataId }));
    navigate("/chat");
  }, [edges, inputDataId, navigate, nodes]);

  // Update node styles based on progress
  const styledNodes = nodes.map((n) => {
    const progress = nodeProgress[n.id];
    if (!progress) return n;
    return {
      ...n,
      data: {
        ...n.data,
        progress: progress.status,
        progressError: progress.error,
      },
    };
  });

  return (
    <div className="pipeline-page">
      <div className="pipeline-sidebar">
        <NodePalette nodeTypes={availableTypes} />

        {templates.length > 0 && (
          <div className="template-list">
            <h3>Templates</h3>
            {templates.map((tpl) => (
              <button
                key={tpl.id}
                className={`template-btn${activeTemplateId === tpl.id ? " active" : ""}`}
                onClick={() => loadTemplate(tpl)}
              >
                {tpl.name}
              </button>
            ))}
            {activeTemplateId && (
              <div className="version-actions">
                <button
                  className="btn-secondary btn-small"
                  onClick={handleSaveVersion}
                  disabled={nodes.length === 0 || !user}
                  title={user ? "Save a new template version" : "Sign in to save template versions"}
                >
                  Save Version
                </button>
                <button
                  className="btn-secondary btn-small"
                  onClick={handleShowHistory}
                  disabled={!user}
                  title={user ? "Show version history" : "Sign in to view template version history"}
                >
                  History
                </button>
                {!user && (
                  <div style={{ fontSize: "0.72rem", color: "var(--color-text-tertiary)", marginTop: 6 }}>
                    Sign in to save template versions and view history.
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        <div className="canvas-actions">
          <h3>Actions</h3>
          <button
            className="delete-btn"
            onClick={handleDelete}
            disabled={selectedCount === 0}
          >
            Delete Selected ({selectedCount})
          </button>
          <button
            className="clear-btn"
            onClick={handleClearAll}
            disabled={nodes.length === 0}
          >
            Clear All
          </button>
          <button
            className="btn-secondary btn-small"
            onClick={handleOpenAI}
            disabled={nodes.length === 0}
            style={{ marginTop: 8 }}
          >
            Ask AI About This Pipeline
          </button>
        </div>

        <div className="run-section">
          <h3>Run Pipeline</h3>
          <label className="input-data-label">
            Input FITS path
            <input
              type="text"
              value={inputDataId}
              onChange={(e) => setInputDataId(e.target.value)}
              className="input-data-input"
            />
          </label>
          <div style={{ fontSize: "0.75rem", color: "var(--color-text-tertiary)", marginTop: "-0.35rem", marginBottom: "0.6rem" }}>
            Used by root Load Data / Import Workspace nodes when their path is left blank.
          </div>
          <button
            className="run-btn"
            onClick={handleRun}
            disabled={nodes.length === 0}
          >
            Run
          </button>
          {runStatus && <div className="run-status">{runStatus}</div>}

          {/* Progress indicator */}
          {Object.keys(nodeProgress).length > 0 && (
            <div className="progress-summary">
              {Object.entries(nodeProgress).map(([nodeId, p]) => (
                <div key={nodeId} className={`progress-item progress-${p.status}`}>
                  <span className="progress-dot" />
                  <span className="progress-label">{nodeId}</span>
                  {p.error && <span className="progress-error">{p.error}</span>}
                </div>
              ))}
            </div>
          )}

          {/* Export buttons — visible when a run has completed */}
          {lastRunId && runStatus?.includes("completed") && (
            <div className="export-section">
              <h4>Export Results</h4>
              <div className="export-buttons">
                <button
                  className="btn-secondary btn-small"
                  onClick={async () => {
                    const blob = await exportRunCSV(lastRunId);
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = `run_${lastRunId}.csv`;
                    a.click();
                    URL.revokeObjectURL(url);
                  }}
                >
                  CSV
                </button>
                <button
                  className="btn-secondary btn-small"
                  onClick={async () => {
                    const blob = await exportRunVOTable(lastRunId);
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = `run_${lastRunId}.votable.xml`;
                    a.click();
                    URL.revokeObjectURL(url);
                  }}
                >
                  VOTable
                </button>
                <button
                  className="btn-secondary btn-small"
                  onClick={async () => {
                    const blob = await exportRunPDF(lastRunId);
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = `run_${lastRunId}.pdf`;
                    a.click();
                    URL.revokeObjectURL(url);
                  }}
                >
                  PDF
                </button>
              </div>
            </div>
          )}

          {/* Run Results Panel */}
          {runResults && lastRunId && (
            <div className="pipeline-results-panel">
              <h4>Run Results</h4>
              {Object.entries(runResults).map(([nodeId, result]) => {
                const r = result as Record<string, unknown>;
                const hasError = !!r.error;
                const nodeType = nodes.find(n => n.id === nodeId)?.data?.nodeType as string
                  || (r.node_id ? String(r.node_id) : null)
                  || nodeId;
                return (
                  <details key={nodeId} className={`pipeline-result-node${hasError ? " result-error" : ""}`} open={hasError}>
                    <summary>
                      <span className={`result-status ${hasError ? "status-err" : "status-ok"}`}>
                        {hasError ? "\u2717" : "\u2713"}
                      </span>
                      {String(nodeType)} <small>({nodeId})</small>
                    </summary>
                    <div className="result-content">
                      {hasError && <div className="result-error-msg">{String(r.error)}</div>}
                      {r.data && typeof r.data === "object" ? (
                        <div className="result-data-summary">
                          {Object.entries(r.data as Record<string, unknown>).map(([k, v]) => (
                            <div key={k} className="result-data-row">
                              <span className="result-key">{k}</span>
                              <span className="result-val">
                                {Array.isArray(v) ? `[${(v as unknown[]).length} items]` : String(v).slice(0, 100)}
                              </span>
                            </div>
                          ))}
                        </div>
                      ) : null}
                      {r.redshift_result ? (
                        <div className="result-highlight">
                          z = {String((r.redshift_result as Record<string, unknown>).best_z)}
                          {" "}(confidence: {String((r.redshift_result as Record<string, unknown>).confidence)})
                        </div>
                      ) : null}
                      {r.sed_fit_result && (r.sed_fit_result as Record<string, unknown>).success ? (
                        <div className="result-highlight">
                          SED: {String((r.sed_fit_result as Record<string, unknown>).model)}
                          {" "}\u03C7\u00B2 = {String((r.sed_fit_result as Record<string, unknown>).reduced_chi_squared)}
                        </div>
                      ) : null}
                      {r.equivalent_width_result ? (
                        <div className="result-highlight">
                          EW = {String((r.equivalent_width_result as Record<string, unknown>).ew_value)} \u00C5
                        </div>
                      ) : null}
                    </div>
                  </details>
                );
              })}
            </div>
          )}
        </div>

        {/* Scheduler */}
        <div className="scheduler-section">
          <h3>
            Scheduling
            <button
              className="btn-secondary btn-small"
              onClick={() => setShowScheduler(!showScheduler)}
              style={{ marginLeft: 8 }}
            >
              {showScheduler ? "Hide" : "Show"}
            </button>
          </h3>
          {showScheduler && (
            <>
              <div className="schedule-form">
                <input
                  type="text"
                  value={scheduleName}
                  onChange={(e) => setScheduleName(e.target.value)}
                  placeholder="Schedule name..."
                  className="input-data-input"
                />
                <select
                  value={scheduleCron}
                  onChange={(e) => setScheduleCron(e.target.value)}
                  className="image-select"
                >
                  <option value="every_30m">Every 30 min</option>
                  <option value="every_hour">Hourly</option>
                  <option value="every_6h">Every 6 hours</option>
                  <option value="daily">Daily</option>
                  <option value="weekly">Weekly</option>
                </select>
                <button
                  className="btn-primary btn-small"
                  disabled={nodes.length === 0 || !scheduleName.trim()}
                  onClick={async () => {
                    const dag = {
                      nodes: nodes.map((n) => ({
                        id: n.id,
                        type: n.data.nodeType,
                        position: n.position,
                        data: { label: n.data.label, params: n.data.params || {} },
                      })),
                      edges: edges.map((e) => ({ id: e.id, source: e.source, target: e.target, ...(e.sourceHandle ? { sourceHandle: e.sourceHandle } : {}) })),
                    };
                    try {
                      const s = await createSchedule(scheduleName, dag, inputDataId, scheduleCron);
                      setSchedules((prev) => [s, ...prev]);
                      setScheduleName("");
                    } catch { /* ignore */ }
                  }}
                >
                  Schedule
                </button>
              </div>
              {schedules.length > 0 && (
                <div className="schedule-list">
                  {schedules.map((s) => (
                    <div key={s.id} className={`schedule-item${s.enabled ? "" : " disabled"}`}>
                      <span className="schedule-name">{s.name}</span>
                      <span className="schedule-cron">{s.cron_expr}</span>
                      <button
                        className="btn-secondary btn-small"
                        onClick={async () => {
                          const res = await toggleSchedule(s.id);
                          setSchedules((prev) =>
                            prev.map((x) => (x.id === s.id ? { ...x, enabled: res.enabled } : x))
                          );
                        }}
                      >
                        {s.enabled ? "Disable" : "Enable"}
                      </button>
                      <button
                        className="btn-secondary btn-small"
                        onClick={async () => {
                          await deleteSchedule(s.id);
                          setSchedules((prev) => prev.filter((x) => x.id !== s.id));
                        }}
                      >
                        Delete
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {importBanner && (
        <div
          style={{
            position: "absolute",
            top: 12,
            left: "50%",
            transform: "translateX(-50%)",
            zIndex: 20,
            padding: "8px 18px",
            borderRadius: 8,
            background: "rgba(48, 209, 88, 0.15)",
            border: "1px solid rgba(48, 209, 88, 0.4)",
            color: "#30d158",
            fontSize: "0.85rem",
            fontWeight: 500,
            whiteSpace: "nowrap",
          }}
        >
          {importBanner}
        </div>
      )}

      <div className="pipeline-canvas" ref={reactFlowWrapper}>
        <ReactFlow
          nodes={styledNodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onInit={setRfInstance}
          onDrop={onDrop}
          onDragOver={onDragOver}
          onNodeClick={handleNodeClick}
          onNodeDoubleClick={handleNodeDoubleClick}
          nodeTypes={nodeTypes}
          deleteKeyCode={["Backspace", "Delete"]}
          fitView
          proOptions={{ hideAttribution: true }}
          connectionRadius={30}
          snapToGrid
          snapGrid={[15, 15]}
          defaultEdgeOptions={{
            type: "smoothstep",
            animated: true,
            style: { stroke: "rgba(255,255,255,0.4)", strokeWidth: 2 },
          }}
          connectionLineStyle={{ stroke: "rgba(10,132,255,0.6)", strokeWidth: 2 }}
          connectionLineType={ConnectionLineType.SmoothStep}
        >
          <Background variant={BackgroundVariant.Dots} gap={24} size={1} color="rgba(255,255,255,0.07)" />
          <Controls />
          <MiniMap
            nodeColor={() => "rgba(255,255,255,0.2)"}
            maskColor="rgba(28, 28, 30, 0.85)"
          />
        </ReactFlow>
      </div>

      {editingNode && (
        <NodeParamsEditor
          nodeId={editingNode.id}
          nodeType={editingNode.data.nodeType as string}
          nodeLabel={editingNode.data.label as string}
          currentParams={(editingNode.data.params as Record<string, unknown>) ?? {}}
          onApply={handleParamsApply}
          workspacePaths={workspacePaths}
          onCancel={() => setEditingNodeId(null)}
        />
      )}

      {inspectResult && (
        <div className="node-result-panel">
          <div className="node-result-panel-header">
            <h4>Node: {inspectResult.nodeId}</h4>
            <button onClick={() => setInspectResult(null)}>&times;</button>
          </div>
          <div className="node-result-panel-body">
            <pre>{JSON.stringify(inspectResult.data, null, 2)}</pre>
          </div>
        </div>
      )}

      {showVersionHistory && (
        <div className="version-history-panel">
          <div className="version-history-header">
            <h3>Version History</h3>
            <div className="version-history-controls">
              <button
                className={`btn-secondary btn-small${compareMode ? " active" : ""}`}
                onClick={() => {
                  setCompareMode(!compareMode);
                  setCompareSelection([]);
                  setDiffResult(null);
                }}
              >
                {compareMode ? "Cancel Compare" : "Compare"}
              </button>
              <button
                className="btn-secondary btn-small"
                onClick={() => {
                  setShowVersionHistory(false);
                  setDiffResult(null);
                  setCompareMode(false);
                  setCompareSelection([]);
                }}
              >
                Close
              </button>
            </div>
          </div>

          {compareMode && (
            <div className="compare-bar">
              <span>Select two versions to compare ({compareSelection.length}/2)</span>
              <button
                className="btn-primary btn-small"
                disabled={compareSelection.length !== 2}
                onClick={handleCompare}
              >
                Show Diff
              </button>
            </div>
          )}

          <div className="version-list">
            {versions.map((v) => (
              <div
                key={v.id}
                className={`version-item${
                  compareMode && compareSelection.includes(v.id) ? " selected" : ""
                }`}
                onClick={() => {
                  if (compareMode) handleToggleCompareSelection(v.id);
                }}
              >
                <div className="version-item-header">
                  <span className="version-number">v{v.version}</span>
                  <span className="version-date">
                    {new Date(v.created_at).toLocaleString()}
                  </span>
                </div>
                {v.change_note && (
                  <div className="version-note">{v.change_note}</div>
                )}
              </div>
            ))}
            {versions.length === 0 && (
              <div className="version-empty">No versions saved yet.</div>
            )}
          </div>

          {diffResult && (
            <div className="diff-view">
              <h4>Diff Result</h4>
              {diffResult.added_nodes.length === 0 &&
                diffResult.removed_nodes.length === 0 &&
                diffResult.modified_nodes.length === 0 &&
                diffResult.added_edges.length === 0 &&
                diffResult.removed_edges.length === 0 && (
                  <div className="diff-empty">No differences found.</div>
                )}
              {diffResult.added_nodes.length > 0 && (
                <div className="diff-section diff-added">
                  <h5>Added Nodes</h5>
                  {diffResult.added_nodes.map((n) => (
                    <div key={n.id} className="diff-item">
                      + {String((n.data as Record<string, unknown>)?.label ?? n.type)} ({n.id})
                    </div>
                  ))}
                </div>
              )}
              {diffResult.removed_nodes.length > 0 && (
                <div className="diff-section diff-removed">
                  <h5>Removed Nodes</h5>
                  {diffResult.removed_nodes.map((n) => (
                    <div key={n.id} className="diff-item">
                      - {String((n.data as Record<string, unknown>)?.label ?? n.type)} ({n.id})
                    </div>
                  ))}
                </div>
              )}
              {diffResult.modified_nodes.length > 0 && (
                <div className="diff-section diff-modified">
                  <h5>Modified Nodes</h5>
                  {diffResult.modified_nodes.map((m) => (
                    <div key={m.id} className="diff-item">
                      ~ {m.id}
                    </div>
                  ))}
                </div>
              )}
              {diffResult.added_edges.length > 0 && (
                <div className="diff-section diff-added">
                  <h5>Added Edges</h5>
                  {diffResult.added_edges.map((edg) => (
                    <div key={edg.id} className="diff-item">
                      + {edg.source} &rarr; {edg.target}
                    </div>
                  ))}
                </div>
              )}
              {diffResult.removed_edges.length > 0 && (
                <div className="diff-section diff-removed">
                  <h5>Removed Edges</h5>
                  {diffResult.removed_edges.map((edg) => (
                    <div key={edg.id} className="diff-item">
                      - {edg.source} &rarr; {edg.target}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
