const CORNERS = [
  { left: -1, top: -1, borderLeft: true, borderTop: true },
  { right: -1, top: -1, borderRight: true, borderTop: true },
  { left: -1, bottom: -1, borderLeft: true, borderBottom: true },
  { right: -1, bottom: -1, borderRight: true, borderBottom: true },
] as const;

export default function Brackets({ color }: { color: string }) {
  return (
    <>
      {CORNERS.map((c, i) => (
        <div
          key={i}
          style={{
            position: "absolute",
            left: "left" in c ? c.left : undefined,
            right: "right" in c ? c.right : undefined,
            top: "top" in c ? c.top : undefined,
            bottom: "bottom" in c ? c.bottom : undefined,
            width: 9,
            height: 9,
            borderLeft: "borderLeft" in c ? `2px solid ${color}` : undefined,
            borderRight: "borderRight" in c ? `2px solid ${color}` : undefined,
            borderTop: "borderTop" in c ? `2px solid ${color}` : undefined,
            borderBottom: "borderBottom" in c ? `2px solid ${color}` : undefined,
          }}
        />
      ))}
    </>
  );
}
