# models.py
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import os
import logging

# 配置日誌 (models.py 內部也使用日誌)
logger = logging.getLogger(__name__)

# 定義資料庫基礎類別
Base = declarative_base()

# 定義專案資料模型
class Project(Base):
    __tablename__ = 'projects' # 資料表名稱

    id = Column(Integer, primary_key=True) # 主鍵，自動遞增
    user_id = Column(String, nullable=False, unique=True, index=True) # 使用者 ID，每個使用者只有一個專案記憶，增加索引提升查詢效率
    project_name = Column(String, default="Default Project") # 專案名稱，可預設
    description = Column(Text, default="") # 專案架構或記憶內容
    progress_notes = Column(Text, default="") # 未來擴充用，可儲存進度筆記
    last_updated = Column(DateTime, default=datetime.now, onupdate=datetime.now) # 最後更新時間

    def __repr__(self):
        return f"<Project(user_id='{self.user_id}', project_name='{self.project_name}')>"

# 設定資料庫連線字串
# 💡 這裡使用環境變數來決定資料庫路徑，方便部署到 Render
#    如果沒有設定，預設會在專案根目錄建立一個 projects.db 檔案
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///projects.db")

# 建立資料庫引擎
engine = create_engine(DATABASE_URL)

# 建立 Session 工廠，用於產生資料庫會話
Session = sessionmaker(bind=engine)

# 💡 這裡不再直接執行 create_all，改由 app.py 在 Flask app_context 中執行，確保正確性
# Base.metadata.create_all(engine)
