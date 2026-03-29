import jmcomic


def searchHandler(jm_config: dict, search_query: str, mode: str) -> str:
    '''
    搜索漫画
    '''
    msg = f'{search_query} 的搜索结果：'

    try:
        option = jmcomic.JmOption.construct(jm_config or {}, cover_default=True)
        client = option.new_jm_client()
    except Exception:
        client_cfg = jm_config.get("client", {}) if jm_config else {}
        domain_list = client_cfg.get("domain", {}).get("api", None)
        impl = client_cfg.get("impl", "api")
        client = jmcomic.JmOption.default().new_jm_client(
            domain_list=domain_list,
            impl=impl,
        )
        
    if mode == "site":  # 站内搜索
        page: jmcomic.JmSearchPage = client.search_site(f"+{search_query}", page=1)
    else:
        return "不支持的搜索模式"
    
    has_result = False
    for album_id, title in page:
        has_result = True
        msg += f'\n\n[{album_id}]: {title}'

    if not has_result:
        return f"{search_query} 暂无搜索结果"

    return msg