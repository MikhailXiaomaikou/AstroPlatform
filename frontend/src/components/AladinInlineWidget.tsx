import AladinViewer from "./viz/AladinViewer";

interface AladinInlineWidgetProps {
  ra: number;
  dec: number;
  name?: string;
}

/**
 * Compact Aladin Lite viewer (300px tall) for embedding in chat messages
 * or other inline contexts. Shows a single marker at the given coordinates.
 */
export default function AladinInlineWidget({ ra, dec, name }: AladinInlineWidgetProps) {
  return (
    <div style={{ margin: "8px 0" }}>
      {name && (
        <div
          style={{
            fontSize: "0.8rem",
            fontWeight: 600,
            marginBottom: 4,
            color: "var(--color-text)",
          }}
        >
          {name}
        </div>
      )}
      <AladinViewer
        ra={ra}
        dec={dec}
        fov={0.1}
        height="300px"
        markers={[
          {
            ra,
            dec,
            name: name ?? `${ra.toFixed(4)}, ${dec.toFixed(4)}`,
            color: "#0A84FF",
          },
        ]}
      />
    </div>
  );
}
