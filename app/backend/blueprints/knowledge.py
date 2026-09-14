# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
育儿知识、健康百科、辅食食谱、活动游戏推荐路由 Blueprint。
提供育儿知识文章、健康百科、辅食食谱、活动游戏推荐等功能。
"""

import datetime
from flask import Blueprint, request, jsonify

from utils import get_db, get_baby_age_months, row_to_dict, rows_to_list, json_body
from user_context import require_admin

bp = Blueprint("knowledge", __name__)


# ==================== API: 育儿知识 ====================

@bp.route('/api/knowledge', methods=['GET'])
def get_knowledge():
    """获取育儿知识文章列表，支持分类与标题/内容搜索"""
    category = request.args.get('category', 'all')
    keyword = request.args.get('q', '')
    db = get_db()

    query = 'SELECT * FROM knowledge_articles WHERE 1=1'
    params = []

    if category and category != 'all':
        query += ' AND category = ?'
        params.append(category)

    if keyword:
        query += ' AND (title LIKE ? OR content LIKE ?)'
        kw = f'%{keyword}%'
        params.extend([kw, kw])

    query += ' ORDER BY created_at DESC'
    rows = db.execute(query, params).fetchall()

    return jsonify({'success': True, 'data': rows_to_list(rows)})


@bp.route('/api/knowledge/<int:article_id>', methods=['GET'])
def get_knowledge_article(article_id):
    """获取单篇育儿知识文章"""
    db = get_db()
    row = db.execute('SELECT * FROM knowledge_articles WHERE id = ?', (article_id,)).fetchone()
    if not row:
        return jsonify({'success': False, 'message': '文章不存在'}), 404
    return jsonify({'success': True, 'data': row_to_dict(row)})


# ==================== API: 育儿知识文章（管理员维护） ====================

@bp.route('/api/knowledge', methods=['POST'])
@require_admin
def create_knowledge_article():
    """新增育儿知识文章（仅管理员）"""
    data = json_body()
    if not data or not data.get('title'):
        return jsonify({'success': False, 'message': '请填写文章标题'}), 400
    title = data.get('title', '').strip()
    if not title:
        return jsonify({'success': False, 'message': '请填写文章标题'}), 400
    db = get_db()
    cursor = db.execute(
        "INSERT INTO knowledge_articles (category, title, age_range, content, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (data.get('category', 'general'), title, data.get('age_range', ''),
         data.get('content', ''),
         datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    )
    db.commit()
    return jsonify({'success': True, 'message': '文章已添加', 'id': cursor.lastrowid})


@bp.route('/api/knowledge/<int:article_id>', methods=['PUT'])
@require_admin
def update_knowledge_article(article_id):
    """更新育儿知识文章（仅管理员）"""
    data = json_body()
    if not data:
        return jsonify({'success': False, 'message': '无效数据'}), 400
    db = get_db()
    row = db.execute('SELECT * FROM knowledge_articles WHERE id = ?', (article_id,)).fetchone()
    if not row:
        return jsonify({'success': False, 'message': '文章不存在'}), 404
    title = data.get('title', '').strip()
    if not title:
        return jsonify({'success': False, 'message': '请填写文章标题'}), 400
    db.execute(
        "UPDATE knowledge_articles SET category=?, title=?, age_range=?, content=? WHERE id=?",
        (data.get('category', 'general'), title, data.get('age_range', ''),
         data.get('content', ''), article_id)
    )
    db.commit()
    return jsonify({'success': True, 'message': '文章已更新'})


@bp.route('/api/knowledge/<int:article_id>', methods=['DELETE'])
@require_admin
def delete_knowledge_article(article_id):
    """删除育儿知识文章（仅管理员）"""
    db = get_db()
    row = db.execute('SELECT * FROM knowledge_articles WHERE id = ?', (article_id,)).fetchone()
    if not row:
        return jsonify({'success': False, 'message': '文章不存在'}), 404
    db.execute('DELETE FROM knowledge_articles WHERE id = ?', (article_id,))
    db.commit()
    return jsonify({'success': True, 'message': '文章已删除'})


# ==================== API: 宝宝健康百科 ====================

@bp.route('/api/wiki', methods=['GET'])
def get_wiki_list():
    """获取健康百科列表"""
    category = request.args.get('category', '')
    keyword = request.args.get('keyword', '')
    db = get_db()

    query = 'SELECT * FROM baby_wiki WHERE 1=1'
    params = []

    if category:
        query += ' AND category = ?'
        params.append(category)

    if keyword:
        query += ' AND (title LIKE ? OR content LIKE ? OR symptoms LIKE ?)'
        kw = f'%{keyword}%'
        params.extend([kw, kw, kw])

    query += ' ORDER BY category, title'
    rows = db.execute(query, params).fetchall()
    return jsonify({'success': True, 'data': rows_to_list(rows)})


@bp.route('/api/wiki/<int:wiki_id>', methods=['GET'])
def get_wiki_detail(wiki_id):
    """获取百科详情"""
    db = get_db()
    row = db.execute('SELECT * FROM baby_wiki WHERE id = ?', (wiki_id,)).fetchone()
    if not row:
        return jsonify({'success': False, 'message': '未找到'}), 404
    return jsonify({'success': True, 'data': row_to_dict(row)})


# ==================== API: 健康百科（管理员维护） ====================

@bp.route('/api/wiki', methods=['POST'])
@require_admin
def create_wiki():
    """新增健康百科条目（仅管理员）"""
    data = json_body()
    if not data or not data.get('title'):
        return jsonify({'success': False, 'message': '请填写标题'}), 400
    title = data.get('title', '').strip()
    if not title:
        return jsonify({'success': False, 'message': '请填写标题'}), 400
    db = get_db()
    cursor = db.execute(
        "INSERT INTO baby_wiki (category, title, content, symptoms, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (data.get('category', 'general'), title, data.get('content', ''),
         data.get('symptoms', ''),
         datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    )
    db.commit()
    return jsonify({'success': True, 'message': '百科条目已添加', 'id': cursor.lastrowid})


@bp.route('/api/wiki/<int:wiki_id>', methods=['PUT'])
@require_admin
def update_wiki(wiki_id):
    """更新健康百科条目（仅管理员）"""
    data = json_body()
    if not data:
        return jsonify({'success': False, 'message': '无效数据'}), 400
    db = get_db()
    row = db.execute('SELECT * FROM baby_wiki WHERE id = ?', (wiki_id,)).fetchone()
    if not row:
        return jsonify({'success': False, 'message': '条目不存在'}), 404
    title = data.get('title', '').strip()
    if not title:
        return jsonify({'success': False, 'message': '请填写标题'}), 400
    db.execute(
        "UPDATE baby_wiki SET category=?, title=?, content=?, symptoms=? WHERE id=?",
        (data.get('category', 'general'), title, data.get('content', ''),
         data.get('symptoms', ''), wiki_id)
    )
    db.commit()
    return jsonify({'success': True, 'message': '百科条目已更新'})


@bp.route('/api/wiki/<int:wiki_id>', methods=['DELETE'])
@require_admin
def delete_wiki(wiki_id):
    """删除健康百科条目（仅管理员）"""
    db = get_db()
    row = db.execute('SELECT * FROM baby_wiki WHERE id = ?', (wiki_id,)).fetchone()
    if not row:
        return jsonify({'success': False, 'message': '条目不存在'}), 404
    db.execute('DELETE FROM baby_wiki WHERE id = ?', (wiki_id,))
    db.commit()
    return jsonify({'success': True, 'message': '百科条目已删除'})


# ==================== API: 辅食食谱 ====================

@bp.route('/api/recipes', methods=['GET'])
def get_recipes():
    """获取辅食食谱列表"""
    db = get_db()
    age_filter = request.args.get('age', '')
    category_filter = request.args.get('category', '')
    search = request.args.get('search', '')

    query = 'SELECT * FROM baby_recipes WHERE 1=1'
    params = []

    if age_filter:
        query += ' AND age_group = ?'
        params.append(age_filter)
    if category_filter:
        query += ' AND category = ?'
        params.append(category_filter)
    if search:
        query += ' AND (title LIKE ? OR ingredients LIKE ?)'
        params.append(f'%{search}%')
        params.append(f'%{search}%')

    query += ' ORDER BY id'

    rows = db.execute(query, params).fetchall()
    recipes = []
    for row in rows:
        r = row_to_dict(row)
        r['ingredients_list'] = [x.strip() for x in (r.get('ingredients') or '').split('、') if x.strip()]
        r['allergens_list'] = [x.strip() for x in (r.get('allergens') or '').split('、') if x.strip() and x.strip() != '无']
        recipes.append(r)

    return jsonify({'success': True, 'data': recipes})


def _age_group_to_months(age_group):
    """将月龄字符串转换为月数，如 '6月+' -> 6, '12月+' -> 12"""
    if not age_group:
        return 0
    import re
    m = re.match(r'(\d+)', str(age_group))
    return int(m.group(1)) if m else 0


@bp.route('/api/recipes/recommended/<int:baby_id>', methods=['GET'])
def get_recommended_recipes(baby_id):
    """根据宝宝月龄智能推荐食谱"""
    db = get_db()
    baby = db.execute('SELECT * FROM babies WHERE id = ?', (baby_id,)).fetchone()
    if not baby:
        return jsonify({'success': False, 'message': '宝宝不存在'}), 404

    # 计算宝宝月龄
    # 注意列名是 birthday（不是 birth_date）——之前取错列名会 KeyError → 500，
    # 导致「按宝宝月龄推荐食谱」整个接口不可用。
    birth_date = baby['birthday']
    if not birth_date:
        return jsonify({'success': True, 'data': [], 'message': '请先设置宝宝出生日期'})

    try:
        from datetime import datetime
        birth = datetime.strptime(birth_date, '%Y-%m-%d')
        now = datetime.now()
        age_months = (now.year - birth.year) * 12 + (now.month - birth.month)
        if now.day < birth.day:
            age_months -= 1
    except Exception:
        return jsonify({'success': True, 'data': [], 'message': '出生日期格式错误'})

    # 获取所有食谱
    rows = db.execute('SELECT * FROM baby_recipes ORDER BY id').fetchall()
    recipes = []
    for row in rows:
        r = row_to_dict(row)
        recipe_age = _age_group_to_months(r.get('age_group', ''))
        # 只推荐适合宝宝当前月龄的食谱
        if recipe_age <= age_months:
            r['ingredients_list'] = [x.strip() for x in (r.get('ingredients') or '').split('、') if x.strip()]
            r['allergens_list'] = [x.strip() for x in (r.get('allergens') or '').split('、') if x.strip() and x.strip() != '无']
            r['_age_match'] = recipe_age  # 用于排序
            recipes.append(r)

    # 按匹配度排序（最接近宝宝月龄的优先）
    recipes.sort(key=lambda x: abs(x['_age_match'] - age_months))
    # 只返回前15个
    recipes = recipes[:15]
    # 移除内部字段
    for r in recipes:
        r.pop('_age_match', None)

    return jsonify({
        'success': True,
        'data': recipes,
        'age_months': age_months,
        'message': f'为您推荐{len(recipes)}个适合{age_months}月龄宝宝的食谱'
    })


@bp.route('/api/recipes/<int:recipe_id>', methods=['GET'])
def get_recipe_detail(recipe_id):
    """获取食谱详情"""
    db = get_db()
    row = db.execute('SELECT * FROM baby_recipes WHERE id = ?', (recipe_id,)).fetchone()
    if not row:
        return jsonify({'success': False, 'message': '食谱不存在'}), 404

    recipe = row_to_dict(row)
    recipe['ingredients_list'] = [x.strip() for x in (recipe.get('ingredients') or '').split('、') if x.strip()]
    recipe['instructions_list'] = [x.strip() for x in (recipe.get('instructions') or '').split('\n') if x.strip()]
    recipe['allergens_list'] = [x.strip() for x in (recipe.get('allergens') or '').split('、') if x.strip() and x.strip() != '无']
    return jsonify({'success': True, 'data': recipe})


@bp.route('/api/recipes', methods=['POST'])
@require_admin
def create_recipe():
    """新增食谱（仅管理员）"""
    data = request.get_json()
    if not data or not data.get('title'):
        return jsonify({'success': False, 'message': '请填写食谱名称'}), 400

    title = data.get('title', '').strip()
    if not title:
        return jsonify({'success': False, 'message': '请填写食谱名称'}), 400

    icon = data.get('icon', '')
    age_group = data.get('age_group', '6月+')
    category = data.get('category', '其他')
    ingredients = data.get('ingredients', '')
    instructions = data.get('instructions', '')
    allergens = data.get('allergens', '无')
    prep_time = data.get('prep_time', '')
    cook_time = data.get('cook_time', '')
    servings = data.get('servings', '')
    nutrition = data.get('nutrition', '')
    tips = data.get('tips', '')
    difficulty = data.get('difficulty', '简单')

    db = get_db()
    cursor = db.execute(
        """INSERT INTO baby_recipes
           (title, icon, age_group, category, ingredients, instructions, allergens,
            prep_time, cook_time, servings, nutrition, tips, difficulty, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (title, icon, age_group, category, ingredients, instructions, allergens,
         prep_time, cook_time, servings, nutrition, tips, difficulty,
         datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    )
    db.commit()
    return jsonify({'success': True, 'message': '食谱已添加', 'id': cursor.lastrowid})


@bp.route('/api/recipes/<int:recipe_id>', methods=['PUT'])
@require_admin
def update_recipe(recipe_id):
    """更新食谱（仅管理员）"""
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': '无效数据'}), 400

    db = get_db()
    row = db.execute('SELECT * FROM baby_recipes WHERE id = ?', (recipe_id,)).fetchone()
    if not row:
        return jsonify({'success': False, 'message': '食谱不存在'}), 404

    title = data.get('title', '').strip()
    if not title:
        return jsonify({'success': False, 'message': '请填写食谱名称'}), 400

    db.execute(
        """UPDATE baby_recipes SET
           title=?, icon=?, age_group=?, category=?, ingredients=?,
           instructions=?, allergens=?, prep_time=?, cook_time=?,
           servings=?, nutrition=?, tips=?, difficulty=?
           WHERE id=?""",
        (title, data.get('icon', ''), data.get('age_group', '6月+'),
         data.get('category', '其他'), data.get('ingredients', ''),
         data.get('instructions', ''), data.get('allergens', '无'),
         data.get('prep_time', ''), data.get('cook_time', ''),
         data.get('servings', ''), data.get('nutrition', ''),
         data.get('tips', ''), data.get('difficulty', '简单'), recipe_id)
    )
    db.commit()
    return jsonify({'success': True, 'message': '食谱已更新'})


@bp.route('/api/recipes/<int:recipe_id>', methods=['DELETE'])
@require_admin
def delete_recipe(recipe_id):
    """删除食谱（仅管理员）"""
    db = get_db()
    row = db.execute('SELECT * FROM baby_recipes WHERE id = ?', (recipe_id,)).fetchone()
    if not row:
        return jsonify({'success': False, 'message': '食谱不存在'}), 404

    db.execute('DELETE FROM baby_recipes WHERE id = ?', (recipe_id,))
    db.commit()
    return jsonify({'success': True, 'message': '食谱已删除'})


# ==================== API: 活动游戏推荐 ====================

@bp.route('/api/activities', methods=['GET'])
def get_activities():
    """获取活动游戏推荐列表"""
    category = request.args.get('category', '')
    age_months = request.args.get('age_months', type=int)

    db = get_db()
    query = 'SELECT * FROM baby_activities WHERE 1=1'
    params = []

    if category:
        query += ' AND category = ?'
        params.append(category)

    if age_months is not None:
        query += ' AND min_age_months <= ? AND max_age_months >= ?'
        params.extend([age_months, age_months])

    query += ' ORDER BY min_age_months, title'
    rows = db.execute(query, params).fetchall()
    return jsonify({'success': True, 'data': rows_to_list(rows)})


@bp.route('/api/activities/<int:activity_id>', methods=['GET'])
def get_activity_detail(activity_id):
    """获取活动详情"""
    db = get_db()
    row = db.execute('SELECT * FROM baby_activities WHERE id = ?', (activity_id,)).fetchone()
    if not row:
        return jsonify({'success': False, 'message': '活动不存在'}), 404

    activity = row_to_dict(row)
    activity['materials_list'] = [x.strip() for x in (activity.get('materials') or '').split('、') if x.strip() and x.strip() != '无']
    activity['steps_list'] = [x.strip() for x in (activity.get('steps') or '').split('\n') if x.strip()]
    activity['benefits_list'] = [x.strip() for x in (activity.get('benefits') or '').split('、') if x.strip()]
    activity['tags_list'] = [x.strip() for x in (activity.get('tags') or '').split(',') if x.strip()]
    return jsonify({'success': True, 'data': activity})


# ==================== API: 活动收藏 ====================

@bp.route('/api/activities/favorites', methods=['GET'])
def get_activity_favorites():
    """获取宝宝的活动收藏列表"""
    baby_id = request.args.get('baby_id', type=int)
    if not baby_id:
        return jsonify({'success': False, 'message': '缺少baby_id参数'}), 400

    db = get_db()
    rows = db.execute(
        '''SELECT a.* FROM baby_activities a
           INNER JOIN activity_favorites f ON a.id = f.activity_id
           WHERE f.baby_id = ?
           ORDER BY f.created_at DESC''',
        (baby_id,)
    ).fetchall()
    return jsonify({'success': True, 'data': rows_to_list(rows)})


@bp.route('/api/activities/favorite', methods=['POST'])
def add_activity_favorite():
    """添加活动收藏"""
    data = json_body()
    baby_id = data.get('baby_id')
    activity_id = data.get('activity_id')

    if not baby_id or not activity_id:
        return jsonify({'success': False, 'message': '缺少必要参数'}), 400

    db = get_db()
    # 检查活动是否存在
    activity = db.execute('SELECT id FROM baby_activities WHERE id = ?', (activity_id,)).fetchone()
    if not activity:
        return jsonify({'success': False, 'message': '活动不存在'}), 404

    # 检查是否已收藏
    existing = db.execute(
        'SELECT id FROM activity_favorites WHERE baby_id = ? AND activity_id = ?',
        (baby_id, activity_id)
    ).fetchone()

    if existing:
        return jsonify({'success': False, 'message': '已收藏该活动'})

    db.execute(
        'INSERT INTO activity_favorites (baby_id, activity_id) VALUES (?, ?)',
        (baby_id, activity_id)
    )
    db.commit()
    return jsonify({'success': True, 'message': '收藏成功'})


@bp.route('/api/activities/favorite', methods=['DELETE'])
def remove_activity_favorite():
    """取消活动收藏"""
    baby_id = request.args.get('baby_id', type=int)
    activity_id = request.args.get('activity_id', type=int)

    if not baby_id or not activity_id:
        return jsonify({'success': False, 'message': '缺少必要参数'}), 400

    db = get_db()
    db.execute(
        'DELETE FROM activity_favorites WHERE baby_id = ? AND activity_id = ?',
        (baby_id, activity_id)
    )
    db.commit()
    return jsonify({'success': True, 'message': '已取消收藏'})


@bp.route('/api/activities/favorite/check', methods=['GET'])
def check_activity_favorite():
    """检查活动是否已收藏"""
    baby_id = request.args.get('baby_id', type=int)
    activity_id = request.args.get('activity_id', type=int)

    if not baby_id or not activity_id:
        return jsonify({'success': False, 'message': '缺少必要参数'}), 400

    db = get_db()
    existing = db.execute(
        'SELECT id FROM activity_favorites WHERE baby_id = ? AND activity_id = ?',
        (baby_id, activity_id)
    ).fetchone()

    return jsonify({'success': True, 'data': {'is_favorite': existing is not None}})


# ==================== API: 活动完成记录 ====================

@bp.route('/api/activities/logs', methods=['GET'])
def get_activity_logs():
    """获取活动完成记录"""
    baby_id = request.args.get('baby_id', type=int)
    activity_id = request.args.get('activity_id', type=int)
    limit = request.args.get('limit', 50, type=int)

    if not baby_id:
        return jsonify({'success': False, 'message': '缺少baby_id参数'}), 400

    db = get_db()
    query = '''SELECT l.*, a.title, a.icon, a.category
               FROM activity_logs l
               INNER JOIN baby_activities a ON l.activity_id = a.id
               WHERE l.baby_id = ?'''
    params = [baby_id]

    if activity_id:
        query += ' AND l.activity_id = ?'
        params.append(activity_id)

    query += ' ORDER BY l.completed_at DESC LIMIT ?'
    params.append(limit)

    rows = db.execute(query, params).fetchall()
    return jsonify({'success': True, 'data': rows_to_list(rows)})


@bp.route('/api/activities/log', methods=['POST'])
def add_activity_log():
    """添加活动完成记录"""
    data = json_body()
    baby_id = data.get('baby_id')
    activity_id = data.get('activity_id')
    duration_minutes = data.get('duration_minutes', 0)
    mood = data.get('mood', 'happy')
    note = data.get('note', '')

    if not baby_id or not activity_id:
        return jsonify({'success': False, 'message': '缺少必要参数'}), 400

    db = get_db()
    # 检查活动是否存在
    activity = db.execute('SELECT id FROM baby_activities WHERE id = ?', (activity_id,)).fetchone()
    if not activity:
        return jsonify({'success': False, 'message': '活动不存在'}), 404

    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db.execute(
        '''INSERT INTO activity_logs (baby_id, activity_id, completed_at, duration_minutes, mood, note)
           VALUES (?, ?, ?, ?, ?, ?)''',
        (baby_id, activity_id, now, duration_minutes, mood, note)
    )
    db.commit()
    return jsonify({'success': True, 'message': '记录已添加'})


@bp.route('/api/activities/log/<int:log_id>', methods=['DELETE'])
def delete_activity_log(log_id):
    """删除活动完成记录"""
    db = get_db()
    db.execute('DELETE FROM activity_logs WHERE id = ?', (log_id,))
    db.commit()
    return jsonify({'success': True, 'message': '记录已删除'})


# ==================== API: 活动统计 ====================

@bp.route('/api/activities/stats', methods=['GET'])
def get_activity_stats():
    """获取活动统计数据"""
    baby_id = request.args.get('baby_id', type=int)
    if not baby_id:
        return jsonify({'success': False, 'message': '缺少baby_id参数'}), 400

    db = get_db()

    # 总完成次数
    total_count = db.execute(
        'SELECT COUNT(*) FROM activity_logs WHERE baby_id = ?', (baby_id,)
    ).fetchone()[0]

    # 今日完成次数
    today = datetime.date.today().strftime('%Y-%m-%d')
    today_count = db.execute(
        'SELECT COUNT(*) FROM activity_logs WHERE baby_id = ? AND completed_at LIKE ?',
        (baby_id, f'{today}%')
    ).fetchone()[0]

    # 本周完成次数
    week_start = (datetime.date.today() - datetime.timedelta(days=datetime.date.today().weekday())).strftime('%Y-%m-%d')
    week_count = db.execute(
        'SELECT COUNT(*) FROM activity_logs WHERE baby_id = ? AND completed_at >= ?',
        (baby_id, week_start)
    ).fetchone()[0]

    # 分类统计
    category_stats = db.execute(
        '''SELECT a.category, COUNT(*) as count
           FROM activity_logs l
           INNER JOIN baby_activities a ON l.activity_id = a.id
           WHERE l.baby_id = ?
           GROUP BY a.category
           ORDER BY count DESC''',
        (baby_id,)
    ).fetchall()

    # 最近7天每日完成数
    daily_stats = []
    for i in range(7):
        day = (datetime.date.today() - datetime.timedelta(days=6-i)).strftime('%Y-%m-%d')
        count = db.execute(
            'SELECT COUNT(*) FROM activity_logs WHERE baby_id = ? AND completed_at LIKE ?',
            (baby_id, f'{day}%')
        ).fetchone()[0]
        daily_stats.append({'date': day, 'count': count})

    return jsonify({
        'success': True,
        'data': {
            'total_count': total_count,
            'today_count': today_count,
            'week_count': week_count,
            'category_stats': rows_to_list(category_stats),
            'daily_stats': daily_stats
        }
    })


# ==================== API: 智能推荐 ====================

@bp.route('/api/activities/recommend', methods=['GET'])
def get_activity_recommend():
    """根据宝宝月龄和活动历史智能推荐活动"""
    baby_id = request.args.get('baby_id', type=int)
    age_months = request.args.get('age_months', type=int)
    limit = request.args.get('limit', 5, type=int)

    if not baby_id:
        return jsonify({'success': False, 'message': '缺少baby_id参数'}), 400

    # age_months 缺失时不应按 0 月龄筛选（否则只会返回新生儿活动），
    # 改为查宝宝生日推算；推算不出则跳过年龄过滤
    db = get_db()
    if age_months is None:
        baby_row = db.execute('SELECT birthday FROM babies WHERE id = ?', (baby_id,)).fetchone()
        if baby_row and baby_row['birthday']:
            try:
                age_months = get_baby_age_months(baby_row['birthday'])
            except Exception:
                age_months = None

    # 获取宝宝已完成的最近活动ID
    recent_activity_ids = db.execute(
        'SELECT DISTINCT activity_id FROM activity_logs WHERE baby_id = ? ORDER BY completed_at DESC LIMIT 10',
        (baby_id,)
    ).fetchall()
    recent_ids = [r[0] for r in recent_activity_ids]

    # 构建查询：匹配月龄且排除最近已玩过的活动
    query = 'SELECT * FROM baby_activities WHERE 1=1'
    params = []
    if age_months is not None:
        query += ' AND min_age_months <= ? AND max_age_months >= ?'
        params.extend([age_months, age_months])

    if recent_ids:
        placeholders = ','.join(['?' for _ in recent_ids])
        query += f' AND id NOT IN ({placeholders})'
        params.extend(recent_ids)

    query += ' ORDER BY RANDOM() LIMIT ?'
    params.append(limit)

    rows = db.execute(query, params).fetchall()

    # 如果推荐数量不足，补充最近玩过的活动
    if len(rows) < limit:
        remaining = limit - len(rows)
        existing_ids = [r['id'] for r in rows] + recent_ids
        placeholders = ','.join(['?' for _ in existing_ids]) if existing_ids else '0'
        extra_query = f'SELECT * FROM baby_activities WHERE 1=1 AND id NOT IN ({placeholders})'
        extra_params = list(existing_ids)
        if age_months is not None:
            extra_query += ' AND min_age_months <= ? AND max_age_months >= ?'
            extra_params.extend([age_months, age_months])
        extra_query += ' ORDER BY RANDOM() LIMIT ?'
        extra_params.append(remaining)
        extra_rows = db.execute(extra_query, extra_params).fetchall()
        rows.extend(extra_rows)

    return jsonify({'success': True, 'data': rows_to_list(rows)})


# ==================== API: 家长指导 ====================

@bp.route('/api/activities/parent-guide', methods=['GET'])
def get_parent_guide():
    """获取家长指导建议"""
    age_months = request.args.get('age_months', type=int)
    if age_months is None:
        return jsonify({'success': False, 'message': '缺少age_months参数'}), 400

    # 根据月龄返回对应的家长指导
    guides = _get_parent_guide_by_age(age_months)
    return jsonify({'success': True, 'data': guides})


def _get_parent_guide_by_age(age_months):
    """根据月龄获取家长指导"""
    if age_months <= 3:
        return {
            'stage': '新生儿期',
            'title': '新生儿期的亲子互动',
            'description': '这个阶段的宝宝主要通过感官来认识世界，亲子互动以感官刺激为主。',
            'tips': [
                '多与宝宝进行眼神交流，距离保持在20-30cm',
                '用温柔的声音和宝宝说话，促进听觉发育',
                '给宝宝做抚触按摩，增进亲子依恋',
                '使用黑白卡进行追视训练，每次2-3分钟',
                '让宝宝趴卧练习抬头，从30秒开始逐渐增加'
            ],
            'warnings': [
                '避免过度刺激，注意观察宝宝的疲劳信号',
                '不要在宝宝困倦时强行进行活动',
                '确保活动环境安全，避免小零件'
            ],
            'daily_schedule': {
                'morning': '追视训练 + 趴卧抬头',
                'afternoon': '抚触按摩 + 听音乐',
                'evening': '亲子共读 + 摇篮曲'
            }
        }
    elif age_months <= 6:
        return {
            'stage': '婴儿早期',
            'title': '探索世界的开始',
            'description': '宝宝开始主动探索周围环境，手眼协调能力快速发展。',
            'tips': [
                '提供不同材质的玩具让宝宝触摸和抓握',
                '玩躲猫猫游戏，帮助理解客体永久性',
                '鼓励宝宝练习翻身和坐立',
                '用简单的词语描述正在做的事情',
                '带宝宝到户外感受大自然'
            ],
            'warnings': [
                '宝宝开始什么都往嘴里放，注意安全',
                '学坐时不要让宝宝坐太久，避免脊柱疲劳',
                '户外活动时注意防晒和防蚊'
            ],
            'daily_schedule': {
                'morning': '趴卧练习 + 抓握游戏',
                'afternoon': '躲猫猫 + 户外散步',
                'evening': '亲子共读 + 音乐律动'
            }
        }
    elif age_months <= 12:
        return {
            'stage': '婴儿晚期',
            'title': '爬行与探索的黄金期',
            'description': '宝宝开始爬行和扶站，探索欲望强烈，需要安全的活动空间。',
            'tips': [
                '为宝宝创造安全的爬行空间',
                '玩逗爬游戏，鼓励宝宝多爬行',
                '提供可以推拉的玩具',
                '教宝宝认识日常物品的名称',
                '鼓励宝宝模仿简单的动作和声音'
            ],
            'warnings': [
                '做好家中安全防护，安装安全门栏',
                '收起危险物品和易碎品',
                '不要让宝宝独自在高处'
            ],
            'daily_schedule': {
                'morning': '爬行练习 + 积木游戏',
                'afternoon': '户外探索 + 认知游戏',
                'evening': '亲子共读 + 音乐律动'
            }
        }
    elif age_months <= 18:
        return {
            'stage': '学步期',
            'title': '蹒跚学步，探索无限',
            'description': '宝宝开始学走路，独立性增强，喜欢模仿大人。',
            'tips': [
                '提供安全的学步环境，鼓励宝宝多走',
                '玩推拉玩具游戏，锻炼平衡能力',
                '教宝宝简单的指令和词汇',
                '鼓励宝宝自己用勺子吃饭',
                '玩过家家和角色扮演游戏'
            ],
            'warnings': [
                '学步时家长要在旁保护',
                '避免使用学步车，可能影响正常发育',
                '注意家具尖角的防护'
            ],
            'daily_schedule': {
                'morning': '户外散步 + 模仿游戏',
                'afternoon': '过家家 + 涂鸦绘画',
                'evening': '亲子共读 + 安静游戏'
            }
        }
    elif age_months <= 24:
        return {
            'stage': '幼儿早期',
            'title': '语言爆发与社交启蒙',
            'description': '宝宝语言能力快速发展，开始有了社交意识。',
            'tips': [
                '多和宝宝对话，鼓励宝宝表达',
                '教宝宝认识颜色、形状和数字',
                '安排和其他小朋友一起玩',
                '鼓励宝宝自己穿脱简单的衣物',
                '通过绘本培养阅读习惯'
            ],
            'warnings': [
                '宝宝可能出现分离焦虑，给予安全感',
                '不要强迫宝宝分享，尊重宝宝的物权',
                '控制屏幕时间，多进行互动游戏'
            ],
            'daily_schedule': {
                'morning': '户外运动 + 认知游戏',
                'afternoon': '社交活动 + 艺术创作',
                'evening': '亲子共读 + 安静游戏'
            }
        }
    else:
        return {
            'stage': '幼儿期',
            'title': '想象力与创造力的发展',
            'description': '宝宝想象力和创造力快速发展，喜欢探索和尝试新事物。',
            'tips': [
                '提供丰富的艺术创作材料',
                '鼓励宝宝参与家务劳动',
                '带宝宝到不同的环境探索',
                '培养宝宝的生活自理能力',
                '通过游戏培养规则意识'
            ],
            'warnings': [
                '宝宝可能开始说谎，这是正常发展阶段',
                '不要对宝宝期望过高，保持耐心',
                '给宝宝足够的自由探索空间'
            ],
            'daily_schedule': {
                'morning': '户外运动 + 科学探索',
                'afternoon': '艺术创作 + 角色扮演',
                'evening': '亲子共读 + 睡前聊天'
            }
        }
