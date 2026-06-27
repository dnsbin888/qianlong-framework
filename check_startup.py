"""潜龙系统启动诊断 — 运行: python check_startup.py"""
import subprocess, sys, os

def check_port(port):
    """检查端口是否被占用"""
    try:
        result = subprocess.run(
            f'netstat -ano | findstr :{port}',
            shell=True, capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            print(f'  ⚠️  端口 {port} 已被占用:')
            for line in result.stdout.strip().split('\n'):
                print(f'      {line.strip()}')
            return True
        else:
            print(f'  ✅ 端口 {port} 空闲')
            return False
    except Exception as e:
        print(f'  ❓ 端口 {port} 检查失败: {e}')
        return False

def check_import(module_name, desc):
    """检查模块能否导入"""
    try:
        __import__(module_name)
        print(f'  ✅ {desc} ({module_name})')
        return True
    except ImportError as e:
        print(f'  ❌ {desc} ({module_name}): {e}')
        return False

def check_py_file(filepath):
    """检查 Python 文件语法"""
    try:
        with open(filepath, 'rb') as f:
            raw = f.read()
        if raw[:3] == b'\xef\xbb\xbf':
            print(f'  ⚠️  {filepath} 有 BOM！需要去除')
            return False
        # 尝试编译
        compile(raw.decode('utf-8-sig'), filepath, 'exec')
        print(f'  ✅ {filepath} 语法OK')
        return True
    except SyntaxError as e:
        print(f'  ❌ {filepath} 语法错误: {e}')
        return False
    except Exception as e:
        print(f'  ❌ {filepath} 读取失败: {e}')
        return False

if __name__ == '__main__':
    print('=' * 60)
    print('  潜龙量化平台 — 启动诊断')
    print('=' * 60)

    # 1. Python 环境
    print('\n📌 Python 环境:')
    print(f'  Python: {sys.version}')
    print(f'  路径: {sys.executable}')

    # 2. 端口检查
    print('\n📌 端口占用:')
    check_port(5000)   # Flask 主站
    check_port(5001)   # Dashboard 看板
    check_port(8501)   # Streamlit

    # 3. 依赖检查
    print('\n📌 依赖库:')
    check_import('flask', 'Flask Web框架')
    check_import('numpy', 'NumPy')
    check_import('pandas', 'Pandas')
    check_import('plotly', 'Plotly图表')
    check_import('streamlit', 'Streamlit仪表盘')

    # 4. 关键文件语法检查
    print('\n📌 关键文件:')
    check_py_file(r'd:\quant_web\app.py')
    check_py_file(r'd:\quant_framework\run_web.py')
    check_py_file(r'd:\quant_framework\quant_dashboard.py')

    # 5. 启动建议
    print('\n' + '=' * 60)
    print('🚀 启动命令:')
    print('=' * 60)
    print()
    print('  方式1 (一键启动):')
    print('    双击: d:\\quant_framework\\启动潜龙量化平台.bat')
    print()
    print('  方式2 (分别启动):')
    print('    终端1: cd /d d:\\quant_web && python -X utf8 app.py')
    print('    终端2: cd /d d:\\quant_framework && python -X utf8 -m streamlit run quant_dashboard.py --server.port 8501 --server.headless true')
    print('    终端3: cd /d d:\\quant_framework && python -X utf8 run_web.py')
    print()
    print('  访问地址:')
    print('    主站:     http://localhost:5000')
    print('    看板:     http://localhost:5001/dashboard')
    print('    仪表盘:   http://localhost:8501')
    print()
