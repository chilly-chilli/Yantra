"""v0.2.1 profiles and extension integration, using local services only."""
import asyncio
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
import pty
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import Field,ValidationError

from yantra.backend import RealModelBackend
from yantra.configuration import ConfigStore
from yantra.engine import Engine,StopRun
from yantra.extensions import ExtensionCatalog,ExtensionRuntime,run_command
from yantra.models import ModelCatalog,ModelProfile,OCROutput,check_model
from yantra.research import RawEngine
from yantra.schemas import FrameworkChoice,MainAction
from test_v2 import PRICE,TEXT,V2TestBackend


MCP_SERVER='''from mcp.server.fastmcp import FastMCP
import os,sys
mcp=FastMCP("local-test",host="127.0.0.1",port=int(sys.argv[1]) if len(sys.argv)>1 else 8000,json_response=True,stateless_http=True)
@mcp.tool()
def echo(text:str)->dict:
    return {"text":text,"secret":os.getenv("SERVER_TOKEN","")}
mcp.run(transport="streamable-http" if len(sys.argv)>1 else "stdio")
'''


class CaptureProvider(BaseLlm):
    model:str="test-capture"
    instructions:list[str]=Field(default_factory=list)
    images:int=0

    async def generate_content_async(self,llm_request,stream=False):
        self.instructions.append(str(llm_request.config.system_instruction))
        self.images+=sum(p.inline_data is not None for c in llm_request.contents for p in c.parts or [])
        payload={"identifier":"quality","reason":"local test"}
        yield LlmResponse(content=types.Content(role="model",parts=[types.Part(text=json.dumps(payload))]))


class ModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_config_selection_and_no_literal_credentials(self):
        with tempfile.TemporaryDirectory() as d:
            store=ConfigStore(Path(d)/"config.json");catalog=ModelCatalog(store)
            profile={"kind":"llm","provider":"openai-compatible","model_id":"local-model","base_url":"http://localhost:1234/v1","api_key_env":"TEST_MODEL_KEY","pricing":PRICE}
            catalog.add("local",profile);catalog.select("llm","local")
            self.assertEqual(catalog.resolve("llm")["model_id"],"local-model")
            self.assertEqual(catalog.resolve("ocr")["name"],"tesseract-auto")
            with self.assertRaises(ValidationError):catalog.add("bad",profile|{"api_key":"do-not-store"})
            self.assertNotIn("do-not-store",store.path.read_text())
            self.assertRaises(ValueError,catalog.select,"ocr","local")

    async def test_model_list_probe_has_no_inference_call(self):
        requests=[]
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path);body=json.dumps({"data":[{"id":"local-model"}]}).encode()
                self.send_response(200);self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with patch.dict(os.environ,{"TEST_MODEL_KEY":"local-test-secret"}):
                profile=ModelProfile(kind="llm",provider="openai-compatible",model_id="local-model",base_url=f"http://127.0.0.1:{server.server_port}/v1",api_key_env="TEST_MODEL_KEY").model_dump()
                checked=await check_model(profile)
                self.assertTrue(checked["ready"]);self.assertEqual(requests,["/v1/models"])
                self.assertNotIn("local-test-secret",json.dumps(checked))
        finally:server.shutdown();server.server_close();thread.join()

    async def test_selected_profile_is_pinned_across_resume(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/"source.txt";source.write_text(TEXT)
            store=ConfigStore(root/"config.json");catalog=ModelCatalog(store)
            profile={"kind":"llm","provider":"openai-compatible","model_id":"first","base_url":"http://localhost:1234/v1","api_key_env":"KEY_ONE","pricing":PRICE}
            catalog.add("one",profile);catalog.add("two",profile|{"model_id":"second","api_key_env":"KEY_TWO"});catalog.select("llm","one")
            with patch("yantra.models.create_backend",return_value=V2TestBackend()) as factory:
                engine=RawEngine.create_raw(root/"runs",{"company":"测试公司","horizon":"12个月","cutoff":"2026-10-13","market":"CN","security_code":"600000"},[source],settings=store)
                self.assertEqual(factory.call_args.args[0]["name"],"one")
            catalog.select("llm","two")
            loaded=Engine.load(root/"runs",engine.state["run_id"])
            with patch("yantra.models.create_backend",return_value=V2TestBackend()) as factory:
                await loaded._decision("choose_framework",{})
                self.assertEqual(factory.call_args.args[0]["name"],"one")
            self.assertEqual(loaded.state["pricing"],engine.state["pricing"])

    async def test_skill_instructions_and_images_reach_native_adk(self):
        backend=RealModelBackend("test","http://unused.invalid/v1","test-only");provider=CaptureProvider();backend._model=provider
        backend.runtime_skills=[{"name":"main","roles":["main"],"content":"MAIN_SKILL","resource_path":"/tmp/main"},
                               {"name":"finance","roles":["finance"],"content":"FINANCE_ONLY","resource_path":"/tmp/finance"}]
        await backend._decision("framework_selector","base",FrameworkChoice,{},image_parts=[types.Part.from_bytes(data=b"image-test",mime_type="image/png")])
        self.assertIn("MAIN_SKILL",provider.instructions[0]);self.assertNotIn("FINANCE_ONLY",provider.instructions[0]);self.assertEqual(provider.images,1)

    async def test_command_ocr_runs_in_material_pipeline_with_budget(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);script=root/"ocr.py"
            script.write_text('import json,sys\nv=json.load(sys.stdin)\nprint(json.dumps({"words":[{"text":"Revenue 1,234.50","bbox":[1,1,200,40]}],"warnings":[]}))\n')
            image=root/"scan.png";Image.new("RGB",(500,150),"white").save(image)
            store=ConfigStore(root/"config.json");catalog=ModelCatalog(store)
            catalog.add("local-ocr",{"kind":"ocr","provider":"command","command":[sys.executable,str(script)],"call_cost_cny":.1,"cost_source":"test fixture fee bound","cost_as_of":"2026-10-13"})
            catalog.select("ocr","local-ocr")
            engine=RawEngine.create_raw(root/"runs",{"company":"测试公司","horizon":"12个月","cutoff":"2026-10-13"},[image],backend=V2TestBackend(),pricing=PRICE,settings=store)
            document=next(iter(engine.state["input_snapshot"]["materials"].values()))
            self.assertIn("1,234.50",document["text"])
            self.assertEqual(engine.state["budget"]["ocr_calls"],1)
            self.assertAlmostEqual(engine.state["budget"]["model_cost_cny"],.1)

    async def test_vision_ocr_requires_explicit_budget_bound(self):
        with self.assertRaises(ValidationError):
            ModelProfile(kind="ocr",provider="vision",model_id="vision-test",base_url="https://unused.invalid/v1")
        self.assertEqual(ModelProfile(kind="llm",provider="openai-compatible",model_id="local",base_url="http://localhost:1234/v1",pricing=PRICE|{"input_per_million":0,"output_per_million":0}).pricing.input_per_million,0)

    async def test_vision_ocr_image_usage_and_own_price_in_material_pipeline(self):
        from PIL import Image
        captured=[]
        async def transcribe(backend,name,instruction,schema,payload,image_parts=None):
            captured.append((name,schema,payload,image_parts))
            backend.last_usage={"input_tokens":1500,"output_tokens":50}
            return {"words":[{"text":"经营现金流 1234.50","bbox":[1,1,300,40]}],"warnings":[]}
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{"VISION_KEY":"private-vision-credential"}):
            root=Path(d);image=root/"scan.png";Image.new("RGB",(500,150),"white").save(image)
            store=ConfigStore(root/"config.json");catalog=ModelCatalog(store)
            price=PRICE|{"input_per_million":20,"output_per_million":30}
            catalog.add("vision",{"kind":"ocr","provider":"vision","model_id":"local-vision","base_url":"http://localhost:1234/v1",
                                  "api_key_env":"VISION_KEY","pricing":price,"max_image_tokens":2048})
            catalog.select("ocr","vision")
            with patch.object(RealModelBackend,"_decision",transcribe):
                engine=RawEngine.create_raw(root/"runs",{"company":"测试公司","horizon":"12个月","cutoff":"2026-10-13"},[image],backend=V2TestBackend(),pricing=PRICE,settings=store)
            receipt=engine.state["model_ledger"][0]
            self.assertEqual(captured[0][:2],("ocr_transcriber",OCROutput))
            self.assertEqual(captured[0][3][0].inline_data.mime_type,"image/png")
            self.assertGreater(receipt["input_token_bound"],2048)
            self.assertEqual(receipt["pricing"]["input_per_million"],20)
            self.assertAlmostEqual(receipt["cost_cny"],.0315)
            self.assertEqual(receipt["status"],"usage_accounted")
            self.assertEqual(engine.state["budget"]["ocr_calls"],1)
            self.assertEqual(engine.state["budget"]["model_calls"],1)
            self.assertIn("1234.50",next(iter(engine.state["input_snapshot"]["materials"].values()))["text"])
            self.assertNotIn("private-vision-credential",(engine.directory/"state.json").read_text())


class ExtensionTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_mcp_discovery_call_schema_and_redaction(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{"TEST_MCP_TOKEN":"private-server-credential"}):
            root=Path(d);server=root/"server.py";server.write_text(MCP_SERVER)
            store=ConfigStore(root/"config.json");catalog=ExtensionCatalog(store)
            await catalog.add("mcp","local",{"transport":"stdio","command":[sys.executable,str(server)],"env_refs":{"SERVER_TOKEN":"TEST_MCP_TOKEN"},"roles":["finance"]})
            runtime=ExtensionRuntime(catalog.snapshot(root/"run"));name=next(iter(runtime.bindings))
            self.assertEqual(len(runtime.permissions("finance")),1);self.assertEqual(runtime.permissions("thesis"),[])
            result=await runtime.call(name,{"text":"hello"},10)
            self.assertIn("hello",json.dumps(result));self.assertNotIn("private-server-credential",json.dumps(result))
            with self.assertRaises(ValueError):await runtime.call(name,{"text":3},10)

    async def test_streamable_http_mcp(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);server=root/"server.py";server.write_text(MCP_SERVER)
            with socket.socket() as sock:sock.bind(("127.0.0.1",0));port=sock.getsockname()[1]
            process=await asyncio.create_subprocess_exec(sys.executable,str(server),str(port),stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
            try:
                for _ in range(100):
                    try:
                        reader,writer=await asyncio.open_connection("127.0.0.1",port);writer.close();await writer.wait_closed();break
                    except OSError:await asyncio.sleep(.05)
                store=ConfigStore(root/"config.json");catalog=ExtensionCatalog(store)
                await catalog.add("mcp","http",{"transport":"streamable-http","url":f"http://127.0.0.1:{port}/mcp","roles":["thesis"]})
                runtime=ExtensionRuntime(catalog.snapshot(root/"run"));name=next(iter(runtime.bindings))
                result=await runtime.call(name,{"text":"http-works"},10)
                self.assertIn("http-works",json.dumps(result))
            finally:
                process.terminate();await process.wait()

    async def test_skills_resources_are_frozen_and_role_scoped(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/"source";source.mkdir();(source/"SKILL.md").write_text("# 用户技能\n遵循现金流口径。")
            (source/"reference.txt").write_text("original resource")
            store=ConfigStore(root/"config.json");catalog=ExtensionCatalog(store)
            await catalog.add("skills","quality",{"path":str(source),"roles":["finance"]})
            snapshot=catalog.snapshot(root/"run");runtime=ExtensionRuntime(snapshot)
            registered=Path(store.load()["skills"]["quality"]["path"])
            registered.write_text("changed later")
            self.assertIn("遵循",snapshot["skills"][0]["content"])
            self.assertEqual(runtime.read_resource("quality","reference.txt","finance")["text"],"original resource")
            self.assertRaises(ValueError,runtime.read_resource,"quality","reference.txt","thesis")
            self.assertRaises(ValueError,runtime.read_resource,"quality","../../config.json","finance")

    async def test_skill_external_symlink_rejected_at_import_and_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/"source";source.mkdir();(source/"SKILL.md").write_text("# Skill")
            outside=root/"outside.txt";outside.write_text("outside resource")
            (source/"link.txt").symlink_to(outside)
            store=ConfigStore(root/"config.json");catalog=ExtensionCatalog(store)
            with self.assertRaises(ValueError):await catalog.add("skills","quality",{"path":str(source)})
            self.assertFalse((root/"skills/quality").exists())
            (source/"link.txt").unlink()
            await catalog.add("skills","quality",{"path":str(source)})
            registered=Path(store.load()["skills"]["quality"]["path"]).parent
            (registered/"link.txt").symlink_to(outside)
            self.assertRaises(ValueError,catalog.snapshot,root/"run")

    async def test_command_timeout_and_secret_redaction(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{"TOOL_KEY":"private-tool-credential"}):
            root=Path(d);script=root/"tool.py";script.write_text('import json,os,sys\nv=json.load(sys.stdin)\nprint(json.dumps({"value":v["value"],"secret":os.environ["TOKEN"]}))\n')
            result=await run_command([sys.executable,str(script)],{"value":42},3,{"TOKEN":"TOOL_KEY"})
            self.assertEqual(result["value"],42);self.assertEqual(result["secret"],"[REDACTED]")
            with self.assertRaises(TimeoutError):await run_command([sys.executable,"-c","import time;time.sleep(60)"],{},.1)

    async def test_command_streamed_json_and_output_limit(self):
        with tempfile.TemporaryDirectory() as d:
            script=Path(d)/"stream.py"
            script.write_text('import json,sys,time\nv=json.load(sys.stdin)\noutput=json.dumps({"text":"x"*v["size"]})\nsys.stdout.write(output[:40000]);sys.stdout.flush();time.sleep(.02)\nsys.stdout.write(output[40000:]);sys.stdout.flush()\n')
            output=await run_command([sys.executable,str(script)],{"size":100000},3)
            self.assertEqual(len(output["text"]),100000)
            with self.assertRaises(ValueError):await run_command([sys.executable,str(script)],{"size":1000001},3)

    async def test_registered_tool_executes_through_native_worker_and_budget(self):
        from yantra.adk_app import run_worker
        class ToolBackend(V2TestBackend):
            async def worker(self,task,local):
                if not local["tool_outputs"]:return self._tool("ext_echo",{"value":42})
                return self._result(task,local,data={"extension_checked":True})
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);tool=root/"tool.py";tool.write_text('import json,sys\nv=json.load(sys.stdin)\nprint(json.dumps({"value":v["value"]}))\n')
            store=ConfigStore(root/"config.json");catalog=ExtensionCatalog(store)
            await catalog.add("tools","echo",{"description":"Echo a value","command":[sys.executable,str(tool)],"input_schema":{"type":"object","properties":{"value":{"type":"integer"}},"required":["value"]},"roles":["finance"],"cost_cny":.2,"cost_kind":"model","cost_source":"fixture","cost_as_of":"2026-10-13"})
            source=root/"source.txt";source.write_text(TEXT)
            engine=RawEngine.create_raw(root/"runs",{"company":"测试公司","horizon":"12个月","cutoff":"2026-10-13","market":"CN","security_code":"600000"},[source],backend=ToolBackend(),pricing=PRICE,settings=store)
            task=engine._make_task(MainAction(action="delegate_task",target="finance",purpose="collect"))
            result=await run_worker(engine,task.model_dump())
            self.assertTrue(result["data"]["extension_checked"])
            self.assertEqual(engine.state["budget"]["tool_calls"],1)
            self.assertTrue(any(r.get("method")=="extension" and r["reserved_cny"]==.2 for r in engine.state["model_ledger"]))
            self.assertTrue(any(m['origin']=='extension:ext_echo' for m in engine.tools.load_manifest()['materials']))
            task.target="thesis";task.authorized_tools=engine.permissions['thesis']
            execution={"task":task,"local":{"actual_calls":0},"started":__import__('time').monotonic(),"failures":{}}
            self.assertRaises(StopRun,engine._prepare_worker_tool,execution,"ext_echo",{"value":42})
            catalog.enable("tools","echo",False)
            self.assertIn("ext_echo",engine.extensions.bindings)

    async def test_mcp_executes_through_native_worker_with_source_receipt(self):
        from yantra.adk_app import run_worker
        class MCPBackend(V2TestBackend):
            alias=""
            async def worker(self,task,local):
                if not local["tool_outputs"]:return self._tool(self.alias,{"text":"worker mcp receipt"})
                response=local["tool_outputs"][0]["response"]
                return self._result(task,local,data={"mcp_checked":response["ok"]})
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);server=root/"server.py";server.write_text(MCP_SERVER)
            store=ConfigStore(root/"config.json");catalog=ExtensionCatalog(store)
            await catalog.add("mcp","local",{"transport":"stdio","command":[sys.executable,str(server)],"roles":["finance"]})
            source=root/"source.txt";source.write_text(TEXT);backend=MCPBackend()
            engine=RawEngine.create_raw(root/"runs",{"company":"测试公司","horizon":"12个月","cutoff":"2026-10-13","market":"CN","security_code":"600000"},[source],backend=backend,pricing=PRICE,settings=store)
            backend.alias=next(iter(engine.extensions.bindings))
            task=engine._make_task(MainAction(action="delegate_task",target="finance",purpose="collect"))
            result=await run_worker(engine,task.model_dump())
            self.assertTrue(result["data"]["mcp_checked"])
            self.assertEqual(engine.state["budget"]["tool_calls"],1)
            self.assertTrue(result["evidence_refs"])
            self.assertEqual(result["calculation_refs"],[])
            materials=engine.tools.load_manifest()["materials"]
            self.assertTrue(any(m["origin"]=="extension:"+backend.alias for m in materials))


class CLITests(unittest.TestCase):
    def interactive(self, arguments, text):
        master, slave = pty.openpty()
        try:
            process = subprocess.Popen(arguments, stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            os.close(slave)
            slave = None
            os.write(master, text.encode())
            stdout, stderr = process.communicate(timeout=45)
            return subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)
        finally:
            os.close(master)
            if slave is not None:
                os.close(slave)

    def test_interactive_selection_and_json_registration(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);config=root/"config.json";profile=root/"ocr.json"
            profile.write_text(json.dumps({"kind":"ocr","provider":"tesseract","language":"eng"}))
            prefix=[sys.executable,"-m","yantra","--config",str(config)]
            added=subprocess.run(prefix+["models","add","english","--file",str(profile)],capture_output=True,text=True)
            self.assertEqual(added.returncode,0,added.stderr)
            selected=self.interactive(prefix+["models", "interactive"], "select\nocr\nenglish\nback\n")
            self.assertEqual(selected.returncode,0,selected.stderr)
            self.assertEqual(ConfigStore(config).load()["selected"]["ocr"],"english")
            listed=subprocess.run(prefix+["models","list"],capture_output=True,text=True)
            self.assertIn("english",listed.stdout)

    def test_interactive_extensions_import_and_disable(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);config=root/"config.json";descriptor=root/"tool.json"
            descriptor.write_text(json.dumps({"description":"CLI fixture","command":[sys.executable,"-c","print('{}')"]}))
            result=self.interactive([sys.executable,"-m","yantra","--config",str(config),"extensions", "interactive"],
                f"add\ntools\nexample\n{descriptor}\nfinance, verification\nlist\ndisable\ntools\nexample\nback\n")
            self.assertEqual(result.returncode,0,result.stderr)
            spec=ConfigStore(config).load()["tools"]["example"]
            self.assertFalse(spec["enabled"]);self.assertEqual(spec["roles"],["finance","verification"])
            self.assertIn("已导入并启用",result.stdout)
