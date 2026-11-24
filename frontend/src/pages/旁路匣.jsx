import React, { useEffect, useState } from "react";
import { 取, 递 } from "../api.js";

export default function 旁路匣页() {
  const [行, 设行] = useState([]);
  const [口, 设口] = useState(null);
  const [选, 设选] = useState("");
  const [报, 设报] = useState(null);
  const 刷 = () => {
    取("/旁路匣/只读").then(设行);
    取("/旁路匣/口径").then(设口);
  };
  useEffect(() => { 刷(); }, []);

  const 详情 = 选 ? 行.find((r) => r.事件编号 === 选) : null;
  const 升格 = async (事件编号) => {
    try {
      设报(await 递("/旁路匣/升格", { 事件编号 }));
    } catch (e) {
      设报({ 失败: String(e.message || e) });
    }
    刷();
  };
  const 解旁路 = async (事件编号, 工单号) => {
    try {
      设报(await 递("/旁路匣/解旁路", { 事件编号, 工单号 }));
    } catch (e) {
      设报({ 失败: String(e.message || e) });
    }
    刷();
  };

  return (
    <div>
      <h1 className="题">⑤ 旁路匣</h1>
      <p className="述">
        水位已越过「该事件所属窗的窗止 + 允许迟到」时，事件不得再改已关窗，落入旁路匣。
        当前口径：<strong>乙 · 可升格暂存</strong>——入流即旁路且不改窗；升格须显式调用，升格成功后才按升格口径入窗。
      </p>
      {口 && (
        <div className="提示条">
          架构 {口.架构}：旁路是否改窗 = <b>{口.旁路是否改窗 ? "是" : "否"}</b>；升格接口是否可调用 ={" "}
          <b>{口.升格是否可调用 ? "是" : "否"}</b>；升格成功后是否改历史窗 ={" "}
          <b>{口.升格后是否改历史窗 ? "是" : "否"}</b>。界戳架构 {口.界戳架构}：迟到写遇戳 = <b>{口["迟到写遇戳"]}</b>；
          解旁路 = <b>{口["解旁路"]}</b>。
        </div>
      )}
      <div className="行"><button className="次" onClick={刷}>刷新旁路</button></div>
      {!行.length ? (
        <div className="空态">暂无旁路。抬水位越过旁路界后再推原窗事件，事件应只进旁路匣。</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>编号</th><th>事件编号</th><th>原因</th><th>水位</th><th>窗止</th><th>旁路界</th>
              <th>升格状态</th><th>戳态</th><th>待办</th><th>升格可调用</th><th>解旁路可调用</th>
              <th>是否改窗</th><th>valid</th><th></th>
            </tr>
          </thead>
          <tbody>
            {行.map((r) => (
              <tr key={r.编号}>
                <td>{r.编号}</td>
                <td className="码">{r.事件编号 || "—"}</td>
                <td>{r.原因}</td>
                <td className="码">{r.水位 || "—"}</td>
                <td className="码">{r.窗止 || "—"}</td>
                <td className="码">{r.旁路界 || "—"}</td>
                <td>{r.升格状态}</td>
                <td>{r.戳态 || "—"}</td>
                <td>{r.待办状态 || "—"}</td>
                <td>{r.升格是否可调用 ? "可" : "否"}</td>
                <td>{r.解旁路是否可调用 ? "可" : "否"}</td>
                <td>{r.是否改窗 ? "是" : "否"}</td>
                <td>
                  <span className={`徽章 ${r.valid ? "通" : "败"}`}>{r.valid ? "有效" : "无效"}</span>
                </td>
                <td>
                  <button className="次" style={{ padding: "0.2rem 0.55rem" }} onClick={() => 设选(r.事件编号)}>
                    详情
                  </button>
                  {r.升格是否可调用 && (
                    <button className="水" style={{ padding: "0.2rem 0.55rem", marginLeft: "0.35rem" }} onClick={() => 升格(r.事件编号)}>
                      升格
                    </button>
                  )}
                  {r.解旁路是否可调用 && (
                    <button className="水" style={{ padding: "0.2rem 0.55rem", marginLeft: "0.35rem" }} onClick={() => 解旁路(r.事件编号, r.工单号)}>
                      解旁路
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {报 && (
        <div className="报箱 码">
          {报.失败 ? 报.失败 : JSON.stringify(报, null, 2)}
        </div>
      )}
      {详情 && (
        <div className="段">
          <div className="段题">旁路只读 · {详情.事件编号}</div>
          <table>
            <tbody>
              {[
                ["事件编号", 详情.事件编号],
                ["房间", 详情.房间键],
                ["原窗键", 详情.窗键],
                ["事件时", 详情.事件时],
                ["旁路时水位", 详情.水位],
                ["当前水位", 详情.当前水位],
                ["窗止", 详情.窗止],
                ["旁路界", 详情.旁路界],
                ["升格状态", 详情.升格状态],
                ["戳号", 详情.戳号],
                ["戳态", 详情.戳态],
                ["待办状态", 详情.待办状态],
                ["工单号", 详情.工单号],
                ["升格是否可调用", 详情.升格是否可调用 ? "是" : "否"],
                ["解旁路是否可调用", 详情.解旁路是否可调用 ? "是" : "否"],
                ["是否改窗", 详情.是否改窗 ? "是" : "否"],
                ["升格是否改历史窗", 详情.升格是否改历史窗 ? "是" : "否"],
                ["valid", 详情.valid ? "是" : "否"],
                ["valid 原因", 详情.valid原因],
              ].map(([k, v]) => (
                <tr key={k}><th style={{ width: "11rem" }}>{k}</th><td className="码">{String(v ?? "—")}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
