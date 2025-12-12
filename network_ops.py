"""
Google Antigravity AIOps Agent - Network Operations Module
"""
import re
import os
import time
import google.generativeai as genai
from netmiko import ConnectHandler

# ★修正: 接続先をより安定した IOS-XE Sandbox に変更
SANDBOX_DEVICE = {
    'device_type': 'cisco_ios',                # NX-OS -> IOS
    'host': 'sandbox-iosxe-recomm-1.cisco.com', # 推奨サーバー
    'username': 'developer',                   # ユーザー名変更
    'password': 'C1sco12345',                  # パスワード変更
    'port': 22,
    'global_delay_factor': 2,
    'banner_timeout': 30,
    'conn_timeout': 30,
}

def sanitize_output(text: str) -> str:
    rules = [
        (r'(password|secret) \d+ \S+', r'\1 <HIDDEN_PASSWORD>'),
        (r'(encrypted password) \S+', r'\1 <HIDDEN_PASSWORD>'),
        (r'(snmp-server community) \S+', r'\1 <HIDDEN_COMMUNITY>'),
        (r'(username \S+ privilege \d+ secret \d+) \S+', r'\1 <HIDDEN_SECRET>'),
        (r'\b(?!(?:10|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.)\d{1,3}\.(?:\d{1,3}\.){2}\d{1,3}\b', '<MASKED_PUBLIC_IP>'),
        (r'([0-9A-Fa-f]{4}\.){2}[0-9A-Fa-f]{4}', '<MASKED_MAC>'),
    ]
    for pattern, replacement in rules:
        text = re.sub(pattern, replacement, text)
    return text

def generate_fake_log_by_ai(scenario_name, target_node, api_key):
    if not api_key: return "Error: API Key Missing"
    genai.configure(api_key=api_key)
    # モデル: gemma-3-12b-it
    model = genai.GenerativeModel(
        "gemma-3-12b-it",
        generation_config={"temperature": 0.0}
    )
    
    vendor = target_node.metadata.get("vendor", "Unknown Vendor")
    os_type = target_node.metadata.get("os", "Unknown OS")
    model_name = target_node.metadata.get("model", "Generic Device")
    hostname = target_node.id

    status_instructions = ""
    if "電源" in scenario_name and "片系" in scenario_name:
        status_instructions = "電源片系故障。PS1:Fail, PS2:OK。通信影響なし。"
    elif "電源" in scenario_name and "両系" in scenario_name:
        status_instructions = "全電源喪失。ログなし(接続不可)またはブートログ。"
    elif "FAN" in scenario_name:
        status_instructions = "FAN故障。Fan1:Fail。温度上昇中だが稼働。"
    elif "メモリ" in scenario_name:
        status_instructions = "メモリリーク。使用率99%。特定プロセスが消費。"
    elif "BGP" in scenario_name:
        status_instructions = "BGPフラッピング。Neighbor StateがIdle/Activeを繰り返す。"
    elif "全回線断" in scenario_name:
        status_instructions = "物理リンクダウン。Interface Down。"

    prompt = f"""
    あなたはネットワーク機器CLIシミュレーターです。
    シナリオ: {scenario_name}
    対象: {hostname} ({vendor} {os_type})
    状態指示: {status_instructions}
    出力: コマンド実行結果の生ログのみ(解説不要)。矛盾なきよう生成せよ。
    """
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"AI Generation Error: {e}"

def generate_config_from_intent(target_node, current_config, intent_text, api_key):
    if not api_key: return "Error: API Key Missing"
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemma-3-12b-it", generation_config={"temperature": 0.0})
    
    vendor = target_node.metadata.get("vendor", "Unknown Vendor")
    os_type = target_node.metadata.get("os", "Unknown OS")
    
    prompt = f"""
    Config生成。対象: {target_node.id} ({vendor} {os_type})
    現状: {current_config}
    意図: {intent_text}
    出力: 投入コマンドのみ(Markdown)
    """
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Config Gen Error: {e}"

def generate_health_check_commands(target_node, api_key):
    if not api_key: return "Error: API Key Missing"
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemma-3-12b-it", generation_config={"temperature": 0.0})
    
    vendor = target_node.metadata.get("vendor", "Unknown Vendor")
    os_type = target_node.metadata.get("os", "Unknown OS")
    
    prompt = f"正常性確認コマンドを3つ生成せよ。対象: {vendor} {os_type}。出力: コマンドのみ箇条書き"
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Command Gen Error: {e}"

def run_diagnostic_simulation(scenario_type, target_node=None, api_key=None):
    time.sleep(1.5)
    
    if "---" in scenario_type or "正常" in scenario_type:
        return {"status": "SKIPPED", "sanitized_log": "No action required.", "error": None}

    if "[Live]" in scenario_type:
        # IOS用の基本コマンドに変更
        commands = ["terminal length 0", "show version", "show ip interface brief", "show ip route"]
        try:
            with ConnectHandler(**SANDBOX_DEVICE) as ssh:
                if not ssh.check_enable_mode(): ssh.enable()
                prompt = ssh.find_prompt()
                raw_output = f"Connected to: {prompt}\n"
                for cmd in commands:
                    output = ssh.send_command(cmd)
                    raw_output += f"\n{'='*30}\n[Command] {cmd}\n{output}\n"
        except Exception as e:
            return {"status": "ERROR", "sanitized_log": "", "error": str(e)}
        return {"status": "SUCCESS", "sanitized_log": sanitize_output(raw_output), "error": None}
            
    elif "全回線断" in scenario_type or "サイレント" in scenario_type or "両系" in scenario_type:
        return {"status": "ERROR", "sanitized_log": "", "error": "Connection timed out"}

    else:
        if api_key and target_node:
            raw_output = generate_fake_log_by_ai(scenario_type, target_node, api_key)
            return {"status": "SUCCESS", "sanitized_log": sanitize_output(raw_output), "error": None}
        else:
            return {"status": "ERROR", "sanitized_log": "", "error": "API Key or Target Node Missing"}
