import requests
import psycopg2
import time

POSTGIS_CONN = "dbname=db user=postgres password=password host=localhost port=5432"
# 换一个更稳定的 Overpass 镜像站 (法兰克福站)
OVERPASS_URL = "https://overpass.kumi.systems/api/interpreter" 

QUERY = """
[out:json][timeout:25];
area["name"="Amsterdam"]->.searchArea;
node["amenity"="cafe"](area.searchArea);
out body;
"""

def main():
    try:
        print("正在从 Overpass 抓取数据 (这可能需要 10-20 秒)...")
        # 增加 timeout，防止无限等待
        response = requests.get(OVERPASS_URL, params={'data': QUERY}, timeout=30)
        
        # 检查是否被限流或出错
        if response.status_code == 429:
            print("❌ 错误：Overpass 服务器现在太忙了，请等 1 分钟再试。")
            return
        
        response.raise_for_status() # 如果状态码不是 200，直接抛出异常
        
        data = response.json()
        elements = data.get('elements', [])
        
        if not elements:
            print("⚠️ 警告：抓取成功但没找到任何数据。请检查区域名称是否正确。")
            return

        # 数据库连接部分保持不变
        conn = psycopg2.connect(POSTGIS_CONN)
        cur = conn.cursor()
        cur.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
        cur.execute("DROP TABLE IF EXISTS cafes;")
        cur.execute("""
            CREATE TABLE cafes (
                id SERIAL PRIMARY KEY,
                name TEXT,
                geometry GEOMETRY(Point, 4326)
            );
        """)
        
        print(f"正在存入 {len(elements)} 条数据到 PostGIS...")
        for el in elements:
            name = el.get('tags', {}).get('name', 'Unnamed Cafe')
            lon, lat = el['lon'], el['lat']
            cur.execute(
                "INSERT INTO cafes (name, geometry) VALUES (%s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))",
                (name, lon, lat)
            )
        
        conn.commit()
        # 关键一步：为地理列创建空间索引（这是工业级搜索极速的关键）
        cur.execute("CREATE INDEX idx_cafes_geometry ON cafes USING GIST(geometry);")
        conn.commit()

        print("✅ 导入成功！")
        cur.close()
        conn.close()
        
    except requests.exceptions.JSONDecodeError:
        print("❌ 错误：服务器没有返回 JSON。可能是被封禁或请求超时。")
    except Exception as e:
        print(f"❌ 出错了: {e}")

if __name__ == "__main__":
    main()