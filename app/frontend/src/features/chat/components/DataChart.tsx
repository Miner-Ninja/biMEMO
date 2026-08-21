import {
  BarChart,
  Bar,
  LineChart,
  Line,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import type { ChartData } from "@/features/chat/model/chart-utils";

const COLORS = [
  "hsl(221, 83%, 53%)", // blue
  "hsl(142, 71%, 45%)", // green
  "hsl(38, 92%, 50%)", // amber
  "hsl(346, 77%, 50%)", // rose
  "hsl(262, 83%, 58%)", // violet
  "hsl(187, 85%, 43%)", // cyan
  "hsl(25, 95%, 53%)", // orange
  "hsl(330, 81%, 60%)", // pink
] as const;

interface DataChartProps {
  chart: ChartData;
}

export function DataChart({ chart }: DataChartProps) {
  const { type, title, x, y, data } = chart;

  if (!data || data.length === 0) return null;

  return (
    <div className="my-3 rounded-lg border bg-card p-4">
      {title && <h4 className="mb-3 text-sm font-medium text-center">{title}</h4>}
      <ResponsiveContainer width="100%" height={300}>
        {type === "bar" ? (
          <BarChart data={data} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
            <XAxis
              dataKey={x}
              tick={{ fontSize: 12 }}
              interval={0}
              angle={data.length > 6 ? -30 : 0}
              textAnchor={data.length > 6 ? "end" : "middle"}
              height={data.length > 6 ? 60 : 30}
            />
            <YAxis tick={{ fontSize: 12 }} />
            <Tooltip />
            <Bar dataKey={y} fill={COLORS[0]} radius={[4, 4, 0, 0]} />
          </BarChart>
        ) : type === "line" ? (
          <LineChart data={data} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
            <XAxis dataKey={x} tick={{ fontSize: 12 }} />
            <YAxis tick={{ fontSize: 12 }} />
            <Tooltip />
            <Line
              type="monotone"
              dataKey={y}
              stroke={COLORS[0]}
              strokeWidth={2}
              dot={{ r: 4 }}
              activeDot={{ r: 6 }}
            />
          </LineChart>
        ) : (
          <PieChart>
            <Pie
              data={data}
              dataKey={y}
              nameKey={x}
              cx="50%"
              cy="50%"
              outerRadius={100}
              label={({ name, percent }: { name?: string; percent?: number }) =>
                `${name ?? ""}: ${((percent ?? 0) * 100).toFixed(0)}%`
              }
              labelLine
            >
              {data.map((_, index) => (
                <Cell key={index} fill={COLORS[index % COLORS.length] ?? COLORS[0]} />
              ))}
            </Pie>
            <Tooltip />
            <Legend />
          </PieChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}
