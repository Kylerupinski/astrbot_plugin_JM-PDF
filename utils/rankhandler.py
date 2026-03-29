import jmcomic


def rankHandler(jm_config: dict, duration: str) -> str:
    '''
    获取漫画排行榜
    '''
    msg = f"{'周' if duration == 'week' else '月'}排行榜"

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
    match duration:
        case "week":
            page: jmcomic.JmCategoryPage = client.week_ranking(1)
        case "month":
            page: jmcomic.JmCategoryPage = client.month_ranking(1)
        case _:
            return "排行榜参数错误，请使用 week 或 month"

    has_result = False
    for album_id, title in page:
        has_result = True
        msg += f'\n\n[{album_id}]: {title}'

    if not has_result:
        return "暂无排行榜数据"

    return msg