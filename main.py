from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import subprocess
import json
import uuid
import os
from typing import List, Dict, Any

# --- 常量定义 ---
SCHEDULER_CPP_DIR = "scheduler_cpp"
SCHEDULER_EXECUTABLE = os.path.join(SCHEDULER_CPP_DIR, "main")
IO_DIRECTORY = f"/tmp/vemu_scheduler_io_{os.getpid()}"

# --- API 数据模型定义 ---

class Core(BaseModel):
    id: int
    type: str

class Resources(BaseModel):
    cores: List[Core]
    memory_size_kb: int = Field(alias="memorySizeKb")

class DAGNode(BaseModel):
    id: str
    name: str
    source_file: str = Field(alias="sourceFile")

class DAGEdge(BaseModel):
    from_node: str = Field(alias="fromNode")
    to_node: str = Field(alias="toNode")
    data_size: int = Field(alias="dataSize")

class DAG(BaseModel):
    nodes: List[DAGNode]
    edges: List[DAGEdge]

class ScheduleRequest(BaseModel):
    """调度请求的完整结构"""
    dag: DAG
    resources: Resources

class ScheduledTaskInput(BaseModel):
    source_variable: str = Field(alias="sourceVariable")
    dest_addr: int = Field(alias="destAddr")
    size: int

class ScheduledTaskOutput(BaseModel):
    source_addr: int = Field(alias="sourceAddr")
    size: int
    dest_variable: str = Field(alias="destVariable")

class ScheduledTask(BaseModel):
    task_id: str = Field(alias="taskId")
    core_id: int = Field(alias="coreId")
    start_cycle: int = Field(alias="startCycle")
    inputs: List[ScheduledTaskInput]
    outputs: List[ScheduledTaskOutput]

class ScheduleResponse(BaseModel):
    """调度成功后返回的调度计划"""
    schedule: List[ScheduledTask]


# --- FastAPI 应用实例 ---
app = FastAPI(
    title="VEMU Scheduling Service",
    description="接收任务DAG和资源信息，生成执行调度计划的服务。",
    version="1.2.0",
)

def convert_dag_to_heft_input(dag: DAG) -> List[Dict[str, Any]]:
    """
    将标准的 DAG 格式转换为 C++ HEFT 调度器期望的输入格式。
    """
    heft_input = []
    
    # 建立一个 taskId -> parents/children 的映射
    adj_list: Dict[str, Dict[str, List[Dict]]] = {
        node.id: {"parentTasks": [], "childTasks": []} for node in dag.nodes
    }

    for edge in dag.edges:
        # C++调度器需要outputIndex和inputIndex, 这里我们用0作为占位符
        # C++调度器还需要outputVar和inputVar, 我们用边连接的节点ID来构造一个名字
        parent_task = {"taskId": edge.from_node, "outputIndex": 0, "outputVar": f"data_from_{edge.from_node}_to_{edge.to_node}", "concat_value": 0, "dest_address": "null"}
        child_task = {"taskId": edge.to_node, "inputIndex": 0, "inputVar": f"data_from_{edge.from_node}_to_{edge.to_node}", "concat_value": 0}
        
        adj_list[edge.to_node]["parentTasks"].append(parent_task)
        adj_list[edge.from_node]["childTasks"].append(child_task)

    for node in dag.nodes:
        task_data = {
            "taskId": node.id,
            # 以下是 C++ 调度器需要的、但标准DAG中没有的字段
            # 我们使用合理的默认值或占位符
            "computationCost": 100, # 占位符
            "spm_size": 1024,       # 占位符
            "num_lane": 4,          # 占位符
            "has_bitalu": 0,        # 占位符
            "has_serdiv": 0,        # 占位符
            "has_complexunit": 0,   # 占位符
            "text_offset": "0x0",   # 占位符
            "data_offset": "0x0",   # 占位符
            "total_length": 0,      # 占位符
            "text_length": 0,       # 占位符
            "data_length": 0,       # 占位符
            "output_num": len(adj_list[node.id]["childTasks"]),
            "hardwareinfo": "0x0",  # 占位符
            "hash": "0x0",          # 占位符
            "parentTasks": adj_list[node.id]["parentTasks"],
            "childTasks": adj_list[node.id]["childTasks"],
            "global_Input": [],     # 暂不支持
            "para_Input": [],       # 暂不支持
            "return_output": [],    # 暂不支持
        }
        heft_input.append(task_data)
        
    return heft_input

def convert_heft_output_to_schedule(heft_output: List[Dict[str, Any]]) -> ScheduleResponse:
    """
    将 C++ HEFT 调度器的输出转换为标准的 ScheduleResponse 格式。
    """
    scheduled_tasks: List[ScheduledTask] = []
    
    # C++调度器的输出是一个任务列表，最后可能跟着一个return_output对象
    for task_data in heft_output:
        # 我们只关心任务对象，它们有 current_taskId 字段
        if "current_taskId" not in task_data:
            continue
            
        task_id = task_data.get("debug_task_name", str(task_data["current_taskId"]))
        
        # 解析输入
        inputs: List[ScheduledTaskInput] = []
        all_input = task_data.get("all_input")
        if isinstance(all_input, list):
            for an_input in all_input:
                dest_addr_str = an_input.get("dest_address", "0")
                try:
                    dest_addr = int(dest_addr_str, 16)
                except (ValueError, TypeError):
                    dest_addr = 0
                
                inputs.append(ScheduledTaskInput(
                    # 使用 varname 作为 source_variable
                    source_variable=an_input.get("varname", "unknown_var"),
                    dest_addr=dest_addr,
                    # HEFT的输出目前没有size信息，使用占位符
                    size=4096 
                ))

        # 解析输出
        # HEFT的输出似乎没有明确的输出字段，这需要Orchestrator根据输入来推断
        # 我们暂时创建一个空的输出列表
        outputs: List[ScheduledTaskOutput] = []

        scheduled_tasks.append(ScheduledTask(
            taskId=task_id,
            # HEFT的输出没有core_id和start_cycle，使用占位符
            core_id=0,
            start_cycle=0,
            inputs=inputs,
            outputs=outputs
        ))
        
    return ScheduleResponse(schedule=scheduled_tasks)

@app.on_event("startup")
async def startup_event():
    """在服务启动时执行的事件"""
    # 确保 C++ 调度器存在并可执行
    if not os.path.exists(SCHEDULER_EXECUTABLE):
        print(f"Scheduler executable not found. Attempting to compile...")
        try:
            # -jN to use N cores for faster compilation
            make_process = subprocess.run(
                ["make", "-j", str(os.cpu_count() or 1)], 
                cwd=SCHEDULER_CPP_DIR, 
                check=True, 
                capture_output=True, 
                text=True
            )
            print("Scheduler C++ code compiled successfully.")
            print(make_process.stdout)
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            error_message = e.stderr if hasattr(e, 'stderr') else str(e)
            raise RuntimeError(
                f"Failed to compile C++ scheduler in '{SCHEDULER_CPP_DIR}'. "
                f"Error: {error_message}"
            )
    # 确保用于数据交换的目录存在
    os.makedirs(IO_DIRECTORY, exist_ok=True)


@app.post("/v1/schedule", 
          response_model=ScheduleResponse,
          summary="生成调度计划",
          description="接收一个DAG和硬件资源描述，调用后端的C++调度算法，生成一个详细的、按时间排序的调度计划。")
async def create_schedule(request: ScheduleRequest):
    """
    此端点是服务的核心。它充当了Web API与后端C++调度算法之间的桥梁。
    """
    # 为本次请求生成一个唯一的ID，用于创建临时文件
    request_id = str(uuid.uuid4())
    input_filepath = os.path.join(IO_DIRECTORY, f"{request_id}_input.json")
    output_filepath = os.path.join(IO_DIRECTORY, f"{request_id}_output.json")

    try:
        # 1. 将API接收的DAG转换为C++程序所需的格式
        heft_input_data = convert_dag_to_heft_input(request.dag)
        with open(input_filepath, "w") as f:
            json.dump(heft_input_data, f, indent=2)

        # 2. 调用C++调度器子进程 (修正了命令行参数)
        command = [ f"./{os.path.basename(SCHEDULER_EXECUTABLE)}", input_filepath, output_filepath ]
        
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            cwd=os.path.dirname(SCHEDULER_EXECUTABLE)
        )

        # 3. 读取并解析调度器的输出
        if not os.path.exists(output_filepath):
            raise HTTPException(
                status_code=500, 
                detail=f"Scheduler did not produce an output file. Stderr: {process.stderr}"
            )

        with open(output_filepath, "r") as f:
            heft_output_data = json.load(f)

        # 4. 返回结果
        return convert_heft_output_to_schedule(heft_output_data)

    except subprocess.CalledProcessError as e:
        # C++ 程序执行失败
        raise HTTPException(
            status_code=500, 
            detail=f"Scheduler C++ process failed with exit code {e.returncode}. Stderr: {e.stderr}"
        )
    except Exception as e:
        # 捕获所有其他错误
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")
    finally:
        # 5. 清理临时文件
        if os.path.exists(input_filepath):
            os.remove(input_filepath)
        if os.path.exists(output_filepath):
            os.remove(output_filepath)


@app.get("/", summary="Health Check")
async def read_root():
    return {"status": "VEMU Scheduling Service is running"} 