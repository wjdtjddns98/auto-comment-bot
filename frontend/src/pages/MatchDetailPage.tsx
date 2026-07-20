import { useParams } from "react-router-dom";

export default function MatchDetailPage() {
  const { id } = useParams();

  return (
    <section>
      <h1>매칭 상세 #{id}</h1>
      <p>템플릿 선택·편집·승인(approve, 409/502 처리), can_write=false 클립보드 복사 — 구현 예정 (P0).</p>
    </section>
  );
}
