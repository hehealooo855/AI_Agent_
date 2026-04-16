def remora_scanner(list_emiten):
    saham_terdeteksi = []
    
    for simbol in list_emiten:
        df = tarik_data_historis(simbol, periode="1mo")
        if df is not None and len(df) > 20:
            vol_sekarang = df['Volume'].iloc[-1]
            avg_vol = df['Volume'].tail(20).mean()
            
            # Deteksi lonjakan volume > 250% (Khas Remora)
            if vol_sekarang > (avg_vol * 2.5):
                change = ((df['Close'].iloc[-1] - df['Close'].iloc[-2]) / df['Close'].iloc[-2]) * 100
                saham_terdeteksi.append({
                    "Simbol": simbol,
                    "Lonjakan Vol": f"{vol_sekarang/avg_vol:.2f}x",
                    "Perubahan Harga": f"{change:.2f}%"
                })
    return saham_terdeteksi

def analisis_ptro_deep_dive():
    ptro = yf.Ticker("PTRO.JK")
    # Mengambil Laba Rugi Terbaru
    income_stmt = ptro.financials
    news = ptro.news[:3] # 3 berita terbaru
    
    prompt = f"""
    Analisis PTRO dari sudut pandang Timothy Ronald:
    1. Laba Rugi: {income_stmt.to_string() if not income_stmt.empty else "Data tidak tersedia"}
    2. Berita Terakhir: {[n['title'] for n in news]}
    3. Kondisi Global: Harga batubara global, inflasi, dan dominasi infrastruktur di Asia.
    
    Berikan narasi apakah PTRO adalah 'The Next Big Thing' dalam siklus hard asset atau hanya kebisingan pasar.
    """
    return model.generate_content(prompt).text