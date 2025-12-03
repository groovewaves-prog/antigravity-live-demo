"""
Google Antigravity AIOps Agent - Network Operations Module
"""
import re
import os
import time
import google.generativeai as genai
from netmiko import ConnectHandler

# Cisco DevNet Sandbox
SANDBOX_DEVICE = {
    'device_type': 'cisco_nxos',
    'host': 'sandbox-nxos-1.cisco.com',
    'username': 'admin',
    'password': 'Admin_1234!',
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

def generate_fake_log_by_ai(scenario_name, api_key):
    """
    シナリオに応じた具体的かつ矛盾のないログを生成する
    """
    if not api_key: return "Error: API Key Missing"
    
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.0-flash")
    
    # 基本設定: デュアル電源を持つ中規模ルータを指定し、矛盾を防ぐ
    target_device_model = "Cisco ISR 4451-X (Dual Power Supply Support)"
    
    # シナリオごとの「演技指導」 (必須出力内容の定義)
    specific_instructions = ""
    
    # 1. 電源障害 (片系/両系)
    if "電源" in scenario_name:
        if "片系" in scenario_name:
            specific_instructions = """
            【必須要件: 電源片系障害】
            1. `show environment` コマンドの結果で以下のように出力すること。
               - Power Supply Module 0: Status **Fail** / **Input Failure** (障害発生)
               - Power Supply Module 1: Status **OK** / **Good** (正常稼働)
            2. インターフェースは全て **UP/UP** (電源冗長により稼働継続)。
            3. Pingは **成功**。
            4. Syslogに `%PEM-3-PEMFAIL: The power supply 0 is faulty` を含める。
            """
        elif "両系" in scenario_name:
            specific_instructions = """
            【必須要件: 電源両系喪失】
            1. ログは「コンソール接続不可」「SSH接続タイムアウト」のエラーメッセージのみとするか、
               あるいは再起動直後の `System returned to ROM by power-on` のようなログにする。
            2. 基本的に通信断状態。
            """

    # 2. FAN故障
    elif "FAN" in scenario_name:
        specific_instructions = """
        【必須要件: FAN故障】
        1. `show environment` コマンドの結果で:
           - Fan 0: **Faulty** / **Stop** (回転数 0 RPM)
           - Fan 1: OK
           - System Temperature: Warning (温度上昇中)
        2. インターフェースは **UP/UP** (稼働継続)。
        3. Syslogに `%ENVMON-3-FAN_FAILED` を含める。
        """

    # 3. メモリリーク
    elif "メモリ" in scenario_name:
        specific_instructions = """
        【必須要件: メモリリーク】
        1. `show processes memory` コマンドの結果で:
           - Processor Pool Total: ... Used: **98%** Free: **2%**
           - 特定のプロセス (例: "BGP Router" や "Chunk Manager") が大量に消費している様子。
        2. インターフェースは UP だが、反応が遅いことを示唆するログがあれば尚良い。
        3. Syslogに `%SYS-2-MALLOCFAIL` を含める。
        """

    # 4. BGPフラッピング
    elif "BGP" in scenario_name:
        specific_instructions = """
        【必須要件: BGPフラッピング】
        1. `show ip bgp summary` の結果で:
           - Neighbor 203.0.113.2 の State/PfxRcd が **Idle** と **Active** を繰り返している。
           - Up/Down 時間が "00:00:05" のように非常に短い。
        2. 物理インターフェースは UP/UP。
        3. Syslogに `%BGP-5-ADJCHANGE: neighbor ... Down` と `Up` が交互に出ているログを含める。
        """

    # 5. WAN全回線断
    elif "全回線断" in scenario_name:
        specific_instructions = """
        【必須要件: WAN全断】
        1. `show ip interface brief` の結果で:
           - GigabitEthernet0/0 (WAN): **DOWN/DOWN**
        2. Ping 8.8.8.8 は **100% loss**。
        """

    prompt = f"""
    あなたはCiscoネットワーク機器のシミュレーターです。
    以下のシナリオに基づき、エンジニアが調査した際のコマンド実行ログを生成してください。

    **発生シナリオ**: {scenario_name}
    **対象機器モデル**: {target_device_model}

    {specific_instructions}

    **共通ルール**:
    - `show version`, `show ip interface brief`, `show environment` (ハードウェア系の場合) 等のコマンド実行結果を含める。
    - 解説やMarkdown装飾は不要。CLIの生テキストのみ出力せよ。
    - 矛盾する情報（例: 電源故障なのにAll OKなど）は絶対に出力しないこと。
    """
    
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"AI Generation Error: {e}"

def run_diagnostic_simulation(scenario_type, api_key=None):
    """診断実行関数"""
    time.sleep(1.5)
    
    status = "SUCCESS"
    raw_output = ""
    error_msg = None

    if "---" in scenario_type or "正常" in scenario_type:
        return {"status": "SKIPPED", "sanitized_log": "No action required.", "error": None}

    # Live実機診断
    if "[Live]" in scenario_type:
        commands = ["terminal length 0", "show version", "show interface brief", "show ip route"]
        try:
            with ConnectHandler(**SANDBOX_DEVICE) as ssh:
                if not ssh.check_enable_mode(): ssh.enable()
                prompt = ssh.find_prompt()
                raw_output += f"Connected to: {prompt}\n"
                for cmd in commands:
                    output = ssh.send_command(cmd)
                    raw_output += f"\n{'='*30}\n[Command] {cmd}\n{output}\n"
        except Exception as e:
            status = "ERROR"
            error_msg = str(e)
            raw_output = f"Real Device Connection Failed: {error_msg}"
            
    # 全断・サイレント（接続不可系）のシミュレーション
    # ※ここで弾くとAI生成ログが作れないので、AIにエラーログを作らせる方針に変更しても良いが、
    #  今回は「接続タイムアウト」を即時返す仕様を維持する
    elif "全回線断" in scenario_type and "WAN" not in scenario_type: # WAN個別ではない広域障害の場合
        status = "ERROR"
        error_msg = "Connection timed out"
        raw_output = "SSH Connection Failed. Host Unreachable."
        
    elif "サイレント" in scenario_type:
        status = "ERROR"
        error_msg = "Connection timed out"
        raw_output = "SSH Connection Failed. Host Unreachable."

    # その他（AI生成）
    else:
        if api_key:
            raw_output = generate_fake_log_by_ai(scenario_type, api_key)
        else:
            status = "ERROR"
            error_msg = "API Key Required"
            raw_output = "Cannot generate logs without API Key."

    return {
        "status": status,
        "sanitized_log": sanitize_output(raw_output),
        "error": error_msg
    }
