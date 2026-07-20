import { useQuery } from "@tanstack/react-query";
import { getHealth } from "../lib/apiClient";

// 하네스 확인용 페이지: 백엔드 /health 를 프록시로 호출해 풀스택 연결을 눈으로 검증한다.
export default function DebugHealthPage() {
  const { data, error, isLoading } = useQuery({ queryKey: ["health"], queryFn: getHealth });

  return (
    <section>
      <h1>백엔드 헬스체크</h1>
      {isLoading && <p>확인 중…</p>}
      {error && <pre style={{ color: "crimson" }}>{String(error)}</pre>}
      {data && <pre>{JSON.stringify(data, null, 2)}</pre>}
    </section>
  );
}
