export interface ChartData {
  type: "bar" | "line" | "pie";
  title: string;
  x: string;
  y: string;
  data: Record<string, unknown>[];
}
