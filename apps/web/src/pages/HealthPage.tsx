import { useQuery } from "@tanstack/react-query";
import { Card, Tag } from "../ui";
import axios from "axios";

interface HealthInfo {
  service: string;
  env: string;
  version: string;
  planner_model: string;
  default_model: string;
}

interface Readiness {
  status: "ok" | "degraded" | "down";
  env: string;
  version: string;
  checks: Record<string, string>;
}

export function HealthPage() {
  const { data: info } = useQuery<HealthInfo>({
    queryKey: ["info"],
    queryFn: async () => (await axios.get("/api/health/info")).data,
  });
  const { data: ready } = useQuery<Readiness>({
    queryKey: ["ready"],
    queryFn: async () => (await axios.get("/api/health/ready")).data,
    refetchInterval: 10_000,
  });

  return (
    <>
      <Card title="Service Info">
        {info ? (
          <ul>
            <li>Service: {info.service}</li>
            <li>Env: {info.env}</li>
            <li>Version: {info.version}</li>
            <li>Planner Model: <Tag color="#1f6feb">{info.planner_model}</Tag></li>
            <li>Default Model: <Tag>{info.default_model}</Tag></li>
          </ul>
        ) : (
          <p>加载中…</p>
        )}
      </Card>
      <Card title="Readiness">
        {ready ? (
          <>
            <p>
              Status: <Tag color={ready.status === "ok" ? "#52c41a" : "#fa8c16"}>{ready.status}</Tag>
            </p>
            <ul>
              {Object.entries(ready.checks).map(([k, v]) => (
                <li key={k}>
                  {k}: {v === "ok" ? <Tag color="#52c41a">ok</Tag> : <Tag color="#d4380d">{v}</Tag>}
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p>检查中…</p>
        )}
      </Card>
    </>
  );
}
