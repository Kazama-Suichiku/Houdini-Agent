"""
Shared Utilities for Houdini Agent
"""

import os
from datetime import datetime

def get_repo_root(start_dir=None):
    """获取仓库根目录（包含 README.md 的目录作为仓库根）"""
    try:
        current = start_dir or os.path.dirname(os.path.abspath(__file__))
        # 向上查找直到找到 README.md 或到达磁盘根
        while True:
            if os.path.exists(os.path.join(current, "README.md")):
                return current
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
    except Exception:
        pass
    # 退化：使用本文件的上级两级
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def get_config_dir():
    """获取统一配置目录: <repo_root>/config"""
    repo_root = get_repo_root()
    config_dir = os.path.join(repo_root, "config")
    os.makedirs(config_dir, exist_ok=True)
    return config_dir

def get_cache_dir():
    """获取统一缓存目录: <repo_root>/cache"""
    repo_root = get_repo_root()
    cache_dir = os.path.join(repo_root, "cache")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir

def load_config(config_name, dcc_type=None):
    """加载配置文件
    
    Args:
        config_name: 配置名称
        dcc_type: DCC 类型 ('houdini', 'maya')，如果为 None 则加载共享配置
    """
    config_dir = get_config_dir()
    
    if dcc_type:
        config_file = f"{dcc_type}_{config_name}.ini"
    else:
        config_file = f"{config_name}.ini"
    
    config_path = os.path.join(config_dir, config_file)
    config = {}
    
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                for line in lines:
                    if ":" in line:
                        key, value = line.strip().split(":", 1)
                        config[key] = value
        except Exception as e:
            print(f"加载配置失败: {e}")
    
    return config, config_path

def save_config(config_name, config, dcc_type=None):
    """保存配置文件
    
    Args:
        config_name: 配置名称
        config: 配置字典
        dcc_type: DCC 类型 ('houdini', 'maya')
    """
    config_dir = get_config_dir()
    
    if dcc_type:
        config_file = f"{dcc_type}_{config_name}.ini"
    else:
        config_file = f"{config_name}.ini"
    
    config_path = os.path.join(config_dir, config_file)
    
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            for key, value in config.items():
                f.write(f"{key}:{value}\n")
        return True, config_path
    except Exception as e:
        print(f"保存配置失败: {e}")
        return False, ""

def get_history_path(history_name, dcc_type=None):
    """获取历史记录文件路径"""
    config_dir = get_config_dir()
    
    if dcc_type:
        history_file = f"{dcc_type}_{history_name}_history.txt"
    else:
        history_file = f"{history_name}_history.txt"
    
    return os.path.join(config_dir, history_file)

def add_to_history(history_name, entry, dcc_type=None):
    """添加记录到历史文件"""
    try:
        history_path = get_history_path(history_name, dcc_type)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        with open(history_path, "a", encoding="utf-8") as f:
            f.write(f"{entry}|{timestamp}\n")
        return True
    except Exception as e:
        print(f"添加历史记录失败: {e}")
        return False

def load_history(history_name, dcc_type=None):
    """加载历史记录"""
    try:
        history_path = get_history_path(history_name, dcc_type)
        if not os.path.exists(history_path):
            return []
            
        with open(history_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        history = []
        for line in lines:
            if "|" in line:
                parts = line.strip().split("|")
                history.append(parts)
                
        return history
    except Exception as e:
        print(f"加载历史记录失败: {e}")
        return []
