from pathlib import Path
from datetime import datetime
import baostock as bs
import pandas as pd


#项目根目录：scr的上一级
project_root = Path(__file__).resolve().parent.parent
raw_dir = project_root / "data" / "raw"
raw_dir.mkdir(parents=True, exist_ok=True)

start_date = "2025-01-01"
end_date = "2025-12-31"

stock = [
    "sh.600000",#浦发银行
    "sh.600519",#贵州茅台
    "sh.601318",#中国平安
    "sz.000001",#平安银行
    "sz.000858"#五粮液
]
fields = (
    "date,code,open,high,low,close,preclose,"
    "volume,amount,adjustflag,turn,tradestatus,pctChg,isST"
)
def download_stock(code:str)-> pd.DataFrame:
    """
    下载单只股票数据，保留原始字段
    :param code: 股票代码
    :return: 股票数据
    """
    rs = bs.query_history_k_data_plus(
        code,
        fields,
        start_date=start_date,
        end_date=end_date,
        frequency="d",
        adjustflag="3"#不复权，原始价格留档
    )
    rows = []
    while (rs.error_code == '0') & rs.next():
        rows.append(rs.get_row_data())
    df = pd.DataFrame(rows, columns=rs.fields)
    return df
def main():
        print("开始下载股票数据...")
        login_result = bs.login()
        if login_result.error_code != '0':
            raise RuntimeError(f"登录失败，错误代码：{login_result.error_code}，错误信息：{login_result.error_msg}")
        try:
            all_data = []
            
            for code in stock:
                df = download_stock(code)
                if not df.empty:
                    all_data.append(df)
            if not all_data:
                raise RuntimeError("未下载到任何股票数据，请检查股票代码或日期范围。")
            # 合并所有股票数据
            result = pd.concat(all_data, ignore_index=True)
            result["date"] = pd.to_datetime(result["date"],errors='coerce')
            #基础检查
            duplicated_counts = result.duplicated(subset=["date", "code"]).sum()
                
            print(f"\n总行数: {len(result)}")
            print(f"股票代码数量: {result['code'].nunique()}")
            print(f"重复行数: {duplicated_counts}")
            print(f"各字段缺失值统计:\n{result.isnull().sum()}")

           #保存数据到CSV文件，不覆盖已有文件
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")                
            output_file = raw_dir / f"stock_data_{timestamp}.csv"
            result.to_csv(output_file, index=False)
            print(f"\n股票数据已保存到: {output_file}")
        finally:
            bs.logout()
            print("已登出baostock。")
if __name__ == "__main__":
    main()
