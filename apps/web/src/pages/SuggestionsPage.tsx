import { useQuery } from "@tanstack/react-query";
import { Card, Tag, Button } from "../ui";
import { SuggestionsApi, type Suggestion } from "../lib/api";
import { AgGridReact } from "ag-grid-react";
import { useMemo } from "react";

export function SuggestionsPage() {
  const { data, refetch } = useQuery({
    queryKey: ["suggestions", "pending"],
    queryFn: () => SuggestionsApi.list({ status: "pending", limit: 200 }),
  });

  const columnDefs = useMemo(
    () => [
      { field: "skill", headerName: "Skill", width: 200 },
      { field: "model", headerName: "Model", width: 100 },
      {
        field: "confidence",
        headerName: "Conf",
        width: 90,
        cellRenderer: (p: { value: number }) => (p.value * 100).toFixed(0) + "%",
      },
      { field: "target_type", headerName: "Target", width: 100 },
      { field: "target_id", headerName: "Target ID", width: 280 },
      { field: "use_case_id", headerName: "Use Case", width: 180 },
      { field: "created_at", headerName: "Created", width: 200 },
      {
        headerName: "Action",
        width: 180,
        cellRenderer: (p: { data: Suggestion }) => (
          <div style={{ display: "flex", gap: 4 }}>
            <Button
              size="sm"
              variant="primary"
              onClick={async () => {
                await SuggestionsApi.approve(p.data.id);
                refetch();
              }}
            >
              批准
            </Button>
            <Button
              size="sm"
              variant="danger"
              onClick={async () => {
                await SuggestionsApi.reject(p.data.id);
                refetch();
              }}
            >
              拒绝
            </Button>
          </div>
        ),
      },
    ],
    [refetch],
  );

  return (
    <Card title={`LLM 建议审核 (${data?.total ?? 0} 待审)`}>
      <div className="ag-theme-quartz" style={{ height: 600, width: "100%" }}>
        <AgGridReact rowData={data?.items ?? []} columnDefs={columnDefs} />
      </div>
    </Card>
  );
}
