import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { AgentNode, GraphEdge } from "../data/types";
import { integrationCatalog, connectorColors } from "../data/mock";
import { usePipelineData } from "../data/store";
import { BrandIcon } from "./BrandIcons";
import { useProject } from "../ProjectContext";

const NODE_W = 132;
const NODE_H = 48;
const GRID = 24;
const CANVAS_H = 408;

type Pos = { x: number; y: number };

function snap(v: number) {
  return Math.round(v / GRID) * GRID;
}

function defaultLayout(nodes: AgentNode[], width: number): Record<string, Pos> {
  const sources = nodes.filter((n) => n.role === "source");
  const primary = nodes.filter((n) => n.role === "primary");
  const subagents = nodes.filter((n) => n.role === "subagent");
  const actions = nodes.filter((n) => n.role === "action");

  // Snap to the grid, then clamp so the snap can never push a node past the
  // right edge of the canvas.
  const maxX = Math.floor((width - NODE_W) / GRID) * GRID;
  const snapClampX = (x: number) => Math.max(0, Math.min(snap(x), maxX));
  const pos: Record<string, Pos> = {};

  // Only the columns that exist get laid out, spread evenly across the canvas
  // so a live run (4 columns) never collides.
  const cols: { list: AgentNode[]; maxGap: number }[] = [
    { list: sources, maxGap: 96 },
    { list: primary, maxGap: 96 },
  ];
  if (subagents.length > 0) cols.push({ list: subagents, maxGap: 72 });
  if (actions.length > 0) cols.push({ list: actions, maxGap: 72 });

  const span = Math.max(width - NODE_W, 0);
  cols.forEach(({ list, maxGap }, ci) => {
    const x =
      cols.length === 2 && ci === 1
        ? width * 0.42 // idle graph: prime sits mid-canvas
        : (span * ci) / Math.max(cols.length - 1, 1);

    const gap = Math.min(maxGap, Math.max(56, (CANVAS_H - 80) / Math.max(list.length, 1)));
    const totalH = (list.length - 1) * gap;
    const startY = Math.max((CANVAS_H - totalH - NODE_H) / 2, 16);

    list.forEach((n, i) => {
      pos[n.id] = { x: snapClampX(x), y: snap(startY + i * gap) };
    });
  });

  return pos;
}

function statusColor(status: string) {
  if (status === "done") return "#069494";
  if (status === "running") return "#FFCE1B";
  return "#5A6170";
}

function statusLabel(status: string, role: string) {
  if (status === "done") return "Done";
  if (status === "running") return "Active";
  return role === "primary" ? "Ready" : "Connected";
}

function curvePath(x1: number, y1: number, x2: number, y2: number) {
  const midX = (x1 + x2) / 2;
  return `M ${x1} ${y1} C ${midX} ${y1}, ${midX} ${y2}, ${x2} ${y2}`;
}

function buildIdleGraph(connectorIds: string[]): { nodes: AgentNode[]; edges: GraphEdge[] } {
  const sources: AgentNode[] = connectorIds
    .map((id): AgentNode | null => {
      const def = integrationCatalog.find((d) => d.id === id);
      if (!def) return null;
      return {
        id: `${id}-src`,
        name: def.name,
        role: "source",
        status: "idle",
        color: connectorColors[id] ?? "#8B97A8",
      };
    })
    .filter((n): n is AgentNode => Boolean(n));

  const prime: AgentNode = {
    id: "prime",
    name: "GhostThread Prime",
    role: "primary",
    status: "idle",
    color: "#FFCE1B",
  };

  const edges: GraphEdge[] = sources.map((s) => ({ from: s.id, to: "prime", active: false }));

  return { nodes: [...sources, prime], edges };
}

export default function AgentGraph() {
  const project = useProject();
  // When a run starts, the backend pushes sub-agent nodes/edges into the
  // store and this graph re-renders with them spawned on the canvas.
  const { activeRun } = usePipelineData();
  const containerRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{
    ids: string[];
    startX: number;
    startY: number;
    origins: Record<string, Pos>;
  } | null>(null);
  const marqueeStartRef = useRef<Pos | null>(null);
  const [draggingIds, setDraggingIds] = useState<string[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [marquee, setMarquee] = useState<{ x: number; y: number; w: number; h: number } | null>(
    null
  );

  const graph = activeRun ?? buildIdleGraph(project?.connectors ?? []);
  const { nodes, edges } = graph;
  const isIdle = activeRun === null;

  // Idle and live-run layouts are stored separately: a run adds columns and
  // repositions everything, and restoring idle positions mid-run collides.
  const storageKey = `ghostthread.graph-layout.${project?.name ?? "default"}${isIdle ? "" : ".run"}`;
  const nodeIds = nodes.map((n) => n.id).join(",");

  const [positions, setPositions] = useState<Record<string, Pos>>({});

  // (Re)build layout when the project or node set changes; keep stored positions.
  useEffect(() => {
    const width = containerRef.current?.clientWidth ?? 960;
    const defaults = defaultLayout(nodes, width);
    let stored: Record<string, Pos> = {};
    try {
      stored = JSON.parse(localStorage.getItem(storageKey) ?? "{}");
    } catch {
      stored = {};
    }
    const merged: Record<string, Pos> = {};
    for (const n of nodes) {
      merged[n.id] = stored[n.id] ?? defaults[n.id];
    }
    setPositions(merged);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageKey, nodeIds]);

  const persist = useCallback(
    (next: Record<string, Pos>) => {
      localStorage.setItem(storageKey, JSON.stringify(next));
    },
    [storageKey]
  );

  const clampPos = useCallback((x: number, y: number): Pos => {
    const w = containerRef.current?.clientWidth ?? 960;
    return {
      x: Math.min(Math.max(x, 0), Math.max(w - NODE_W, 0)),
      y: Math.min(Math.max(y, 0), CANVAS_H - NODE_H),
    };
  }, []);

  const canvasPoint = (e: React.PointerEvent): Pos => {
    const rect = containerRef.current?.getBoundingClientRect();
    return { x: e.clientX - (rect?.left ?? 0), y: e.clientY - (rect?.top ?? 0) };
  };

  // --- Node drag (single node, or the whole selection as a group) ---

  const onNodePointerDown = (e: React.PointerEvent, id: string) => {
    e.stopPropagation();
    if (!positions[id]) return;
    // Grabbing a selected node moves the whole selection; grabbing an
    // unselected one drops the selection and moves just that node.
    const ids = selected.has(id) ? Array.from(selected) : [id];
    if (!selected.has(id)) setSelected(new Set());
    const origins: Record<string, Pos> = {};
    for (const i of ids) origins[i] = positions[i];
    dragRef.current = { ids, startX: e.clientX, startY: e.clientY, origins };
    try {
      (e.currentTarget as Element).setPointerCapture(e.pointerId);
    } catch {
      // Synthetic pointer events (tests) have no active pointer to capture.
    }
    setDraggingIds(ids);
  };

  const onNodePointerMove = (e: React.PointerEvent) => {
    const drag = dragRef.current;
    if (!drag) return;
    const dx = e.clientX - drag.startX;
    const dy = e.clientY - drag.startY;
    setPositions((prev) => {
      const next = { ...prev };
      for (const id of drag.ids) {
        const o = drag.origins[id];
        next[id] = clampPos(o.x + dx, o.y + dy);
      }
      return next;
    });
  };

  const onNodePointerUp = () => {
    const drag = dragRef.current;
    if (!drag) return;
    setPositions((prev) => {
      const next = { ...prev };
      for (const id of drag.ids) {
        const cur = next[id];
        next[id] = clampPos(snap(cur.x), snap(cur.y));
      }
      persist(next);
      return next;
    });
    dragRef.current = null;
    setDraggingIds([]);
  };

  // --- Marquee selection on the canvas background ---

  const onCanvasPointerDown = (e: React.PointerEvent) => {
    const p = canvasPoint(e);
    marqueeStartRef.current = p;
    setMarquee({ x: p.x, y: p.y, w: 0, h: 0 });
    setSelected(new Set());
    try {
      (e.currentTarget as Element).setPointerCapture(e.pointerId);
    } catch {
      // Synthetic pointer events (tests) have no active pointer to capture.
    }
  };

  const onCanvasPointerMove = (e: React.PointerEvent) => {
    const start = marqueeStartRef.current;
    if (!start) return;
    const p = canvasPoint(e);
    const rect = {
      x: Math.min(start.x, p.x),
      y: Math.min(start.y, p.y),
      w: Math.abs(p.x - start.x),
      h: Math.abs(p.y - start.y),
    };
    setMarquee(rect);
    const hit = new Set(
      nodes
        .filter((n) => {
          const pos = positions[n.id];
          if (!pos) return false;
          return (
            pos.x < rect.x + rect.w &&
            pos.x + NODE_W > rect.x &&
            pos.y < rect.y + rect.h &&
            pos.y + NODE_H > rect.y
          );
        })
        .map((n) => n.id)
    );
    setSelected(hit);
  };

  const onCanvasPointerUp = () => {
    marqueeStartRef.current = null;
    setMarquee(null);
  };

  const resetLayout = () => {
    const width = containerRef.current?.clientWidth ?? 960;
    const defaults = defaultLayout(nodes, width);
    setPositions(defaults);
    persist(defaults);
  };

  const edgePaths = useMemo(
    () =>
      edges
        .map((edge) => {
          const from = positions[edge.from];
          const to = positions[edge.to];
          if (!from || !to) return null;
          return {
            key: `${edge.from}-${edge.to}`,
            d: curvePath(from.x + NODE_W, from.y + NODE_H / 2, to.x, to.y + NODE_H / 2),
            active: edge.active,
          };
        })
        .filter((e): e is { key: string; d: string; active: boolean } => Boolean(e)),
    [edges, positions]
  );

  return (
    <div className="rounded-lg border border-border bg-panel">
      <div className="flex items-center justify-between px-5 pt-4 pb-3">
        <h2 className="text-[11px] font-semibold text-muted uppercase tracking-wider">
          Agent Pipeline
        </h2>
        <div className="flex items-center gap-4">
          {!isIdle && (
            <div className="flex items-center gap-4 text-[11px] text-muted">
              <span className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-accent" /> Done
              </span>
              <span className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-sun" /> Active
              </span>
              <span className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-muted" /> Idle
              </span>
            </div>
          )}
          <button
            onClick={resetLayout}
            className="text-[11px] text-muted hover:text-dim transition-colors cursor-pointer"
          >
            Reset layout
          </button>
        </div>
      </div>

      {/* Canvas */}
      <div
        ref={containerRef}
        onPointerDown={onCanvasPointerDown}
        onPointerMove={onCanvasPointerMove}
        onPointerUp={onCanvasPointerUp}
        className="relative mx-3 mb-3 rounded-md border border-border/60 overflow-hidden select-none touch-none"
        style={{
          height: CANVAS_H,
          backgroundImage: "radial-gradient(circle, #3A3A44 1px, transparent 1px)",
          backgroundSize: `${GRID}px ${GRID}px`,
          backgroundPosition: "0 0",
        }}
      >
        {/* Soft vignette so the dots fade at the edges */}
        <div
          className="absolute inset-0 pointer-events-none"
          style={{
            background:
              "radial-gradient(ellipse at center, transparent 55%, rgba(17,17,19,0.9) 100%)",
          }}
        />

        {/* Edges follow node positions live */}
        <svg className="absolute inset-0 w-full h-full pointer-events-none">
          {edgePaths.map((edge) => (
            <g key={edge.key}>
              <path
                d={edge.d}
                fill="none"
                stroke={edge.active ? "#069494" : "#3A3A42"}
                strokeWidth={edge.active ? 1.5 : 1.2}
                strokeDasharray={edge.active ? undefined : "5 5"}
                opacity={edge.active ? 0.7 : 0.8}
              />
              {edge.active && (
                <circle r="3" fill="#069494" opacity={0.9}>
                  <animateMotion dur="2.4s" repeatCount="indefinite" path={edge.d} />
                </circle>
              )}
            </g>
          ))}
        </svg>

        {/* Nodes */}
        {nodes.map((node) => {
          const pos = positions[node.id];
          if (!pos) return null;
          const isPrimary = node.role === "primary";
          const isSource = node.role === "source";
          const isDragging = draggingIds.includes(node.id);
          const isSelected = selected.has(node.id);
          const connectorId = node.id.replace(/-src$/, "");

          return (
            <div
              key={node.id}
              onPointerDown={(e) => onNodePointerDown(e, node.id)}
              onPointerMove={onNodePointerMove}
              onPointerUp={onNodePointerUp}
              className={`absolute flex items-center gap-2.5 px-3 rounded-lg border touch-none ${
                isSelected ? "animate-strobe " : "transition-shadow duration-150 "
              }${
                isDragging
                  ? "cursor-grabbing z-20 shadow-[0_12px_32px_rgba(0,0,0,0.5)] border-accent/60 bg-panel2"
                  : isSelected
                    ? "cursor-grab z-20 border-accent bg-panel2"
                    : "cursor-grab z-10 hover:border-border-light " +
                      (isPrimary
                        ? "bg-[#1a1708] border-sun/40"
                        : "bg-panel2 border-border-light/60")
              }`}
              style={{
                left: pos.x,
                top: pos.y,
                width: NODE_W,
                height: NODE_H,
                transform: isDragging ? "scale(1.03)" : undefined,
              }}
            >
              <span className="w-6 h-6 rounded-md bg-bg/60 border border-white/8 flex items-center justify-center flex-shrink-0 pointer-events-none">
                {isPrimary ? (
                  <img src="/ghostthread-logo.png" alt="" className="w-4 h-4 object-contain" />
                ) : isSource ? (
                  <BrandIcon id={connectorId} className="w-3.5 h-3.5" />
                ) : (
                  <svg className="w-3.5 h-3.5" viewBox="0 0 16 16" fill="none" stroke={node.color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M8 1.5l1.8 4.2L14 7.5l-4.2 1.8L8 13.5 6.2 9.3 2 7.5l4.2-1.8L8 1.5z" />
                  </svg>
                )}
              </span>
              <span className="flex-1 min-w-0 pointer-events-none">
                <span className={`block truncate text-text leading-tight ${
                  isPrimary ? "text-[11.5px] font-semibold" : "text-[12px] font-medium"
                }`}>
                  {node.name}
                </span>
                <span
                  className="block text-[9px] font-medium leading-tight"
                  style={{ color: statusColor(node.status) }}
                >
                  {statusLabel(node.status, node.role)}
                </span>
              </span>
              <span
                className="w-1.5 h-1.5 rounded-full flex-shrink-0 pointer-events-none"
                style={{ backgroundColor: statusColor(node.status) }}
              />
            </div>
          );
        })}

        {/* Marquee selection box */}
        {marquee && (
          <div
            className="absolute border border-accent/70 bg-accent/10 rounded-sm pointer-events-none z-30"
            style={{ left: marquee.x, top: marquee.y, width: marquee.w, height: marquee.h }}
          />
        )}

        {/* Hint */}
        <div className="absolute bottom-2.5 left-3 text-[10px] text-muted/80 pointer-events-none">
          {selected.size > 0
            ? `${selected.size} selected \u00b7 drag any highlighted node to move them together \u00b7 click empty space to clear`
            : "Drag nodes to arrange \u00b7 drag empty space to box-select \u00b7 snaps to grid"}
        </div>
      </div>

      {isIdle && (
        <div className="text-center text-[12px] text-muted pb-4 -mt-1">
          No pipeline runs yet. Sub-agents will spawn on the canvas when a complaint is processed.
        </div>
      )}
    </div>
  );
}
