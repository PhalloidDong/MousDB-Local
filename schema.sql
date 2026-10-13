-- MousDB Local v1.4.1 数据库结构
--
-- 设计说明：
-- 1. 小鼠的系统编号 code 是给实验人员看的稳定编号；内部关联统一使用整数 id。
-- 2. 历史记录优先保留，因此字典和笼位被引用后不做级联删除。
-- 3. movement、event 等从属于小鼠的记录，在数据库层允许随小鼠删除；但 v1.4.1
--    的界面和 API 根本不提供删除小鼠功能，避免误删科研记录。

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rooms (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    building TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS racks (
    id INTEGER PRIMARY KEY,
    room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE RESTRICT,
    name TEXT NOT NULL COLLATE NOCASE,
    capacity INTEGER CHECK (capacity IS NULL OR capacity > 0),
    note TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (room_id, name)
);

CREATE TABLE IF NOT EXISTS cages (
    id INTEGER PRIMARY KEY,
    rack_id INTEGER NOT NULL REFERENCES racks(id) ON DELETE RESTRICT,
    code TEXT NOT NULL COLLATE NOCASE UNIQUE,
    capacity INTEGER NOT NULL DEFAULT 5 CHECK (capacity > 0),
    purpose TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strains (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    -- name_html 只保存经过后端白名单清洗的 i/em/sup/sub/b/strong/u 标签。
    -- name 是去掉标签后的纯文本，用于唯一约束、搜索和 CSV 导出。
    name_html TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mouse_lines (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    name_html TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS genotypes (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    name_html TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mice (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL COLLATE NOCASE UNIQUE,
    ear_tag TEXT NOT NULL DEFAULT '',
    sex TEXT NOT NULL DEFAULT 'U' CHECK (sex IN ('M', 'F', 'U')),
    dob TEXT,
    status TEXT NOT NULL DEFAULT 'Active'
        CHECK (status IN ('Active', 'Experimental', 'Transferred', 'Dead')),
    strain_id INTEGER REFERENCES strains(id) ON DELETE RESTRICT,
    line_id INTEGER REFERENCES mouse_lines(id) ON DELETE RESTRICT,
    genotype_id INTEGER REFERENCES genotypes(id) ON DELETE RESTRICT,
    generation TEXT NOT NULL DEFAULT '',
    coat_color TEXT NOT NULL DEFAULT '',
    cage_id INTEGER REFERENCES cages(id) ON DELETE RESTRICT,
    father_id INTEGER REFERENCES mice(id) ON DELETE RESTRICT,
    mother_id INTEGER REFERENCES mice(id) ON DELETE RESTRICT,
    source_type TEXT NOT NULL DEFAULT 'Other'
        CHECK (source_type IN ('Import', 'Bred', 'Transfer', 'Other')),
    arrival_date TEXT,
    death_date TEXT,
    death_reason TEXT NOT NULL DEFAULT '',
    transfer_date TEXT,
    transfer_destination TEXT NOT NULL DEFAULT '',
    transfer_reason TEXT NOT NULL DEFAULT '',
    experimental_status TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT '',
    project TEXT NOT NULL DEFAULT '',
    comment TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (father_id IS NULL OR father_id <> id),
    CHECK (mother_id IS NULL OR mother_id <> id)
);

CREATE TABLE IF NOT EXISTS mouse_movements (
    id INTEGER PRIMARY KEY,
    mouse_id INTEGER NOT NULL REFERENCES mice(id) ON DELETE CASCADE,
    from_cage_id INTEGER REFERENCES cages(id) ON DELETE RESTRICT,
    to_cage_id INTEGER REFERENCES cages(id) ON DELETE RESTRICT,
    moved_at TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS matings (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL COLLATE NOCASE UNIQUE,
    male_id INTEGER NOT NULL REFERENCES mice(id) ON DELETE RESTRICT,
    cage_id INTEGER REFERENCES cages(id) ON DELETE RESTRICT,
    start_date TEXT NOT NULL,
    end_date TEXT,
    status TEXT NOT NULL DEFAULT 'Active'
        CHECK (status IN ('Active', 'Ended', 'Paused')),
    offspring_strain_id INTEGER REFERENCES strains(id) ON DELETE RESTRICT,
    offspring_line_id INTEGER REFERENCES mouse_lines(id) ON DELETE RESTRICT,
    purpose TEXT NOT NULL DEFAULT '',
    project TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mating_females (
    mating_id INTEGER NOT NULL REFERENCES matings(id) ON DELETE CASCADE,
    female_id INTEGER NOT NULL REFERENCES mice(id) ON DELETE RESTRICT,
    PRIMARY KEY (mating_id, female_id)
);

CREATE TABLE IF NOT EXISTS litters (
    id INTEGER PRIMARY KEY,
    mating_id INTEGER NOT NULL REFERENCES matings(id) ON DELETE RESTRICT,
    -- v1.4.1 修复：一雄多雌时必须明确这一窝由哪只母鼠生产。
    -- 为兼容无法追溯母鼠的旧数据，数据库允许 NULL；新建 API 强制要求填写。
    mother_id INTEGER REFERENCES mice(id) ON DELETE RESTRICT,
    code TEXT NOT NULL COLLATE NOCASE UNIQUE,
    birth_date TEXT NOT NULL,
    total_born INTEGER NOT NULL DEFAULT 0 CHECK (total_born >= 0),
    male_count INTEGER NOT NULL DEFAULT 0 CHECK (male_count >= 0),
    female_count INTEGER NOT NULL DEFAULT 0 CHECK (female_count >= 0),
    unknown_count INTEGER NOT NULL DEFAULT 0 CHECK (unknown_count >= 0),
    dead_count INTEGER NOT NULL DEFAULT 0 CHECK (dead_count >= 0),
    weaned_date TEXT,
    status TEXT NOT NULL DEFAULT 'Born' CHECK (status IN ('Born', 'Weaned', 'Lost')),
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (total_born = male_count + female_count + unknown_count + dead_count)
);

CREATE TABLE IF NOT EXISTS mouse_events (
    id INTEGER PRIMARY KEY,
    mouse_id INTEGER NOT NULL REFERENCES mice(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    event_date TEXT NOT NULL,
    title TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id INTEGER,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_racks_room ON racks(room_id);
CREATE INDEX IF NOT EXISTS idx_cages_rack ON cages(rack_id);
CREATE INDEX IF NOT EXISTS idx_mice_status ON mice(status);
CREATE INDEX IF NOT EXISTS idx_mice_cage ON mice(cage_id);
CREATE INDEX IF NOT EXISTS idx_mice_strain ON mice(strain_id);
CREATE INDEX IF NOT EXISTS idx_mice_line ON mice(line_id);
CREATE INDEX IF NOT EXISTS idx_mice_parents ON mice(father_id, mother_id);
CREATE INDEX IF NOT EXISTS idx_movements_mouse ON mouse_movements(mouse_id, moved_at);
CREATE INDEX IF NOT EXISTS idx_events_mouse ON mouse_events(mouse_id, event_date);
CREATE INDEX IF NOT EXISTS idx_litters_status_birth ON litters(status, birth_date);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
