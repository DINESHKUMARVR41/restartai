import json, logging, os, re
from typing import Any
import httpx

logger = logging.getLogger(__name__)

class AIService:
    def __init__(self):
        self.gemini_key=os.getenv("GEMINI_API_KEY","").strip()
        self.groq_key=os.getenv("GROQ_API_KEY","").strip()
        self.gemini_model=os.getenv("GEMINI_MODEL","gemini-3.8-flash").strip()
        self.groq_model=os.getenv("GROQ_MODEL","openai/gpt-oss-120b").strip()
        self.gemini_url=f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent"
        self.groq_url="https://api.groq.com/openai/v1/chat/completions"

    def status(self):
        return {"gemini_configured":bool(self.gemini_key),"groq_configured":bool(self.groq_key),
                "gemini_model":self.gemini_model,"groq_model":self.groq_model}

    @staticmethod
    def _json(text):
        try:
            x=json.loads((text or "").strip()); return x if isinstance(x,dict) else {}
        except Exception:
            m=re.search(r"\{.*\}",text or "",re.S)
            if not m:return {}
            try:
                x=json.loads(m.group(0)); return x if isinstance(x,dict) else {}
            except Exception:return {}

    async def _gemini_json(self,prompt):
        if not self.gemini_key:return {}, "not configured"
        payload={"contents":[{"parts":[{"text":prompt}]}],
                 "generationConfig":{"temperature":0.1,"responseMimeType":"application/json"}}
        try:
            async with httpx.AsyncClient(timeout=45) as c:
                r=await c.post(self.gemini_url,headers={"x-goog-api-key":self.gemini_key,"Content-Type":"application/json"},json=payload)
            if r.is_error:return {},f"HTTP {r.status_code}"
            d=r.json(); text=d.get("candidates",[{}])[0].get("content",{}).get("parts",[{}])[0].get("text","")
            x=self._json(text); return x,None if x else "no usable JSON"
        except Exception as e:return {},str(e)

    async def _groq_json(self,prompt):
        if not self.groq_key:return {}, "not configured"
        payload={"model":self.groq_model,"messages":[{"role":"system","content":"You are RestartAI's industrial procurement decision assistant. Never invent supplier facts."},{"role":"user","content":prompt}],
                 "temperature":0.1,"max_completion_tokens":1200,"response_format":{"type":"json_object"}}
        try:
            async with httpx.AsyncClient(timeout=45) as c:
                r=await c.post(self.groq_url,headers={"Authorization":f"Bearer {self.groq_key}","Content-Type":"application/json"},json=payload)
            if r.is_error:return {},f"HTTP {r.status_code}"
            d=r.json(); text=d.get("choices",[{}])[0].get("message",{}).get("content","")
            x=self._json(text); return x,None if x else "no usable JSON"
        except Exception as e:return {},str(e)

    @staticmethod
    def _score(o,req):
        if not(o.get("compatible") and o.get("confirmed") and (o.get("quantity_available") or 0)>0): return -1
        need=max(float(req.get("quantity") or 1),1); cov=min((o.get("quantity_available") or 0)/need,1)
        price=0.5 if o.get("unit_price") is None else 1/(1+max(float(o["unit_price"]),0)/1000)
        speed=0.5 if o.get("availability_hours") is None else 1/(1+max(float(o["availability_hours"]),0))
        return round(.5*cov+.3*price+.2*speed,6)

    async def rank_suppliers(self,request,offers,plan):
        candidates=[]
        for o in offers:
            x=dict(o); x["deterministic_score"]=self._score(x,request)
            if x["deterministic_score"]>=0:candidates.append(x)
        if not candidates:
            return {"success":False,"best_supplier":None,"ranking":[],"decision":"No confirmed supplier offer is available yet.","providers":self.status(),"fallback_used":True}
        facts=[{k:o.get(k) for k in ["supplier","quantity_available","unit_price","currency","availability_hours","delivery_method","compatible","confirmed","notes","deterministic_score"]} for o in candidates]
        prompt=f"""Select the best supplier for an urgent factory recovery.
Incident: {json.dumps(request,ensure_ascii=False)}
Offers are factual CALL-E results: {json.dumps(facts,ensure_ascii=False)}
Plan: {json.dumps(plan or {},ensure_ascii=False)}
Hard constraints: cover quantity, respect maximum recovery time, respect product compatibility. Prefer lower purchase cost and downtime exposure. Never invent missing facts.
Return only JSON: {{"best_supplier":"name or null","ranking":[{{"supplier":"name","score":0-100,"reason":"short factual reason"}}],"decision":"short recommendation","risk":"short risk"}}"""
        import asyncio
        gr,qq=await asyncio.gather(self._gemini_json(prompt),self._groq_json(prompt))
        gem,ge=gr; gro,gqe=qq
        names={o["supplier"] for o in candidates}; choice=None
        for r in (gem,gro):
            if r.get("best_supplier") in names: choice=r["best_supplier"]; break
        if choice is None: choice=max(candidates,key=lambda x:x["deterministic_score"])["supplier"]
        airows={}
        for r in (gem,gro):
            for row in r.get("ranking",[]) or []:
                if isinstance(row,dict) and row.get("supplier") in names: airows[row["supplier"]]=row
        ranking=[]
        for o in sorted(candidates,key=lambda x:(x["supplier"]==choice,x["deterministic_score"]),reverse=True):
            row=airows.get(o["supplier"],{})
            ranking.append({"supplier":o["supplier"],"score":row.get("score",round(o["deterministic_score"]*100,1)),
                            "reason":row.get("reason","Strong confirmed combination of coverage, price and delivery.")})
        return {"success":True,"best_supplier":choice,"ranking":ranking,
                "decision":gem.get("decision") or gro.get("decision") or f"{choice} is the strongest confirmed option.",
                "risk":gem.get("risk") or gro.get("risk") or "No additional AI risk assessment returned.",
                "providers":{"gemini":{"configured":bool(self.gemini_key),"used":bool(gem),"error":ge},
                            "groq":{"configured":bool(self.groq_key),"used":bool(gro),"error":gqe}},
                "fallback_used":not bool(gem or gro)}

    async def _groq_text(self,prompt):
        if not self.groq_key:return None
        try:
            p={"model":self.groq_model,"messages":[{"role":"user","content":prompt}],"temperature":.2,"max_completion_tokens":700}
            async with httpx.AsyncClient(timeout=45) as c:
                r=await c.post(self.groq_url,headers={"Authorization":f"Bearer {self.groq_key}","Content-Type":"application/json"},json=p)
            if r.is_error:return None
            return r.json().get("choices",[{}])[0].get("message",{}).get("content","").strip() or None
        except Exception:return None

    async def _gemini_text(self,prompt):
        if not self.gemini_key:return None
        try:
            p={"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"temperature":.2,"maxOutputTokens":700}}
            async with httpx.AsyncClient(timeout=45) as c:
                r=await c.post(self.gemini_url,headers={"x-goog-api-key":self.gemini_key,"Content-Type":"application/json"},json=p)
            if r.is_error:return None
            return r.json().get("candidates",[{}])[0].get("content",{}).get("parts",[{}])[0].get("text","").strip() or None
        except Exception:return None

    async def chat(self,message,context=None):
        prompt=f"""You are RestartAI, an emergency production recovery assistant.
Use only the supplied dashboard context for supplier facts. Never invent inventory, prices, calls, or approvals. If unavailable, say so.
Context: {json.dumps(context or {},ensure_ascii=False)}
User: {message}
Answer concisely."""
        text=await self._groq_text(prompt)
        if text:return {"success":True,"provider":"Groq","answer":text}
        text=await self._gemini_text(prompt)
        if text:return {"success":True,"provider":"Gemini","answer":text}
        return {"success":True,"provider":"Local","answer":"AI chat is not configured. Add GEMINI_API_KEY or GROQ_API_KEY; the live recovery dashboard still works."}
