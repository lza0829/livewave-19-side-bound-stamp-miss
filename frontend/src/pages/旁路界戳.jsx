import React, { useEffect, useState } from "react";
import { 取 } from "../api.js";

export default function 旁路界戳() {
  const [rows, setRows] = useState(null);
  const [验, set验] = useState(null);
  const [err, setErr] = useState("");
  const reload = () => {
    取("/核证/旁路界戳").then(setRows).catch((e) => setErr(String(e.message || e)));
    取("/核证/旁路界戳/核验").then(set验).catch((e) => setErr(String(e.message || e)));
  };
  useEffect(() => { reload(); }, []);
  return (
    <div>
      <h1 className="题">旁路界戳核证</h1>
      <p className="述">
        硬约束：禁止旁路成功戳空；禁止同事件既在匣又改累计；禁止戳号被邻事件顶替。
        核证接口为只读，不改任何状态。
      </p>
      {err && <pre className="码">{err}</pre>}
      <button className="次" onClick={reload}>刷新</button>
      {验 && (
        <div className="提示条">
          架构 <b>{验.架构}</b>：迟到写遇戳 = <b>{验["迟到写遇戳"]}</b>；戳态推进 ={" "}
          <b>{验["戳态推进"] ? "是" : "否"}</b>；解旁路 = <b>{验["解旁路"]}</b>。
          <span className={`徽章 ${验.核证 && 验.核证.是否通过 ? "通" : "败"}`} style={{ marginLeft: "0.5rem" }}>
            {验.核证 && 验.核证.是否通过 ? "硬约束通过" : "硬约束失败"}
          </span>
        </div>
      )}
      {验 && 验.核证 && !验.核证.是否通过 && (
        <pre className="码">{JSON.stringify(验.核证.问题, null, 2)}</pre>
      )}
      <pre className="码">{rows ? JSON.stringify(rows, null, 2) : "（空）"}</pre>
    </div>
  );
}
