-- ============================================================
-- Graduation Requirements Analysis System DB Schema
-- PostgreSQL 15+
-- ============================================================

-- Extensions
CREATE EXTENSION IF NOT EXISTS "pgcrypto"; -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "pg_trgm";  -- text similarity search
CREATE EXTENSION IF NOT EXISTS "unaccent"; -- search normalization helper
CREATE EXTENSION IF NOT EXISTS "vector";   -- pgvector: VECTOR(1536)

-- ============================================================
-- 1. students
-- ============================================================
CREATE TABLE students (
    student_id      VARCHAR(12)  PRIMARY KEY,
    name            VARCHAR(50)  NOT NULL,
    major           VARCHAR(100) NOT NULL,
    sub_major       VARCHAR(100),
    admission_year  SMALLINT     NOT NULL,
    is_transfer     BOOLEAN      NOT NULL DEFAULT FALSE,
    is_eng_cert     BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE students IS '재학생 기본 정보';
COMMENT ON COLUMN students.student_id IS '학번 (로그인 식별자)';
COMMENT ON COLUMN students.is_eng_cert IS '공학인증 트랙 이수 여부 (별도 졸업요건 적용)';

-- ============================================================
-- 2. courses
-- ============================================================
CREATE TABLE courses (
    course_code     VARCHAR(20)  PRIMARY KEY,
    name            VARCHAR(200) NOT NULL,
    credits         SMALLINT     NOT NULL CHECK (credits > 0),
    area_type       VARCHAR(50)  NOT NULL,
    sub_area        VARCHAR(100),
    building_name   VARCHAR(100),
    latitude        NUMERIC(10,7),
    longitude       NUMERIC(10,7),
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE courses IS '전체 과목 마스터 테이블';
COMMENT ON COLUMN courses.area_type IS '이수구분: 기교/균교/특교/대교/전필/전선/심전/자선';
COMMENT ON COLUMN courses.latitude IS '건물 GPS 좌표 - 동선 최적화에 사용';

-- ============================================================
-- 3. completed_courses
-- ============================================================
CREATE TABLE completed_courses (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id      VARCHAR(12)  NOT NULL REFERENCES students(student_id) ON DELETE CASCADE,
    course_code     VARCHAR(20)  NOT NULL REFERENCES courses(course_code),
    area_type       VARCHAR(50)  NOT NULL,
    credits         SMALLINT     NOT NULL CHECK (credits > 0),
    grade           VARCHAR(5)   NOT NULL,
    semester_taken  VARCHAR(10)  NOT NULL,
    is_substituted  BOOLEAN      NOT NULL DEFAULT FALSE,
    substituted_by  UUID         REFERENCES completed_courses(id),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_cc_student ON completed_courses(student_id);
CREATE INDEX idx_cc_area ON completed_courses(student_id, area_type);

COMMENT ON TABLE completed_courses IS '학생별 이수 과목 (성적표 파싱 결과)';
COMMENT ON COLUMN completed_courses.area_type IS 'AnalysisEngine 버킷 배분 후 확정된 이수구분';
COMMENT ON COLUMN completed_courses.is_substituted IS '교과목 대체 승인을 통해 인정된 이수';

-- ============================================================
-- 4. graduation_requirements
-- ============================================================
CREATE TABLE graduation_requirements (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    major               VARCHAR(100) NOT NULL,
    admission_year      SMALLINT     NOT NULL,
    is_eng_cert         BOOLEAN      NOT NULL DEFAULT FALSE,
    basic_ge            SMALLINT     NOT NULL DEFAULT 0,
    balanced_ge         SMALLINT     NOT NULL DEFAULT 0,
    specialized_ge      SMALLINT     NOT NULL DEFAULT 0,
    univ_core_ge        SMALLINT     NOT NULL DEFAULT 0,
    major_required      SMALLINT     NOT NULL DEFAULT 0,
    major_elective      SMALLINT     NOT NULL DEFAULT 0,
    advanced_major      SMALLINT     NOT NULL DEFAULT 0,
    general_elective    SMALLINT     NOT NULL DEFAULT 0,
    total_credits       SMALLINT     NOT NULL DEFAULT 130,
    min_ge_areas        SMALLINT     NOT NULL DEFAULT 0,
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    UNIQUE (major, admission_year, is_eng_cert)
);

COMMENT ON TABLE graduation_requirements IS '학번 연도 + 전공 + 공학인증 여부별 졸업 요건 (GraduationRequirement 클래스 매핑)';

-- ============================================================
-- 5. analysis_results
-- ============================================================
CREATE TABLE analysis_results (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id      VARCHAR(12)  NOT NULL REFERENCES students(student_id) ON DELETE CASCADE,
    requirement_id  UUID         NOT NULL REFERENCES graduation_requirements(id),
    result_json     JSONB        NOT NULL,
    deficiency_map  JSONB        NOT NULL DEFAULT '{}',
    overflow_map    JSONB        NOT NULL DEFAULT '{}',
    analyzed_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_ar_student ON analysis_results(student_id, analyzed_at DESC);

COMMENT ON TABLE analysis_results IS 'AnalysisEngine 분석 결과 (최신 1건 = 현재 졸업 현황)';
COMMENT ON COLUMN analysis_results.deficiency_map IS 'calculateDeficiency() 반환값 저장';

-- ============================================================
-- 6. course_offerings
-- ============================================================
CREATE TABLE course_offerings (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    course_code         VARCHAR(20)  NOT NULL REFERENCES courses(course_code),
    semester            VARCHAR(10)  NOT NULL,
    section             VARCHAR(20)  NOT NULL,
    professor           VARCHAR(100),
    schedule            VARCHAR(200),
    building_name       VARCHAR(100),
    room_number         VARCHAR(20),
    raw_classroom       TEXT,
    offering_college    VARCHAR(100),
    offering_department VARCHAR(100),
    offering_major      VARCHAR(100),
    target_departments  TEXT,
    is_remote           BOOLEAN      NOT NULL DEFAULT FALSE,
    language_type       VARCHAR(100),
    course_note         TEXT,
    is_cancelled        BOOLEAN      NOT NULL DEFAULT FALSE,
    current_enrollment  SMALLINT     NOT NULL DEFAULT 0,
    max_enrollment      SMALLINT     NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    UNIQUE (course_code, semester, section)
);

CREATE INDEX idx_co_semester ON course_offerings(semester);

COMMENT ON TABLE course_offerings IS '학기별 개설 강좌 (시간표·동선 최적화 데이터 소스)';
COMMENT ON COLUMN course_offerings.schedule IS '요일+교시 문자열 - Time-Space 최적화 파싱 대상';
COMMENT ON COLUMN course_offerings.section IS '분반';
COMMENT ON COLUMN course_offerings.raw_classroom IS '원본 강의실 문자열 (시간/건물/호실 혼합)';
COMMENT ON COLUMN course_offerings.target_departments IS '원본 대상학과 및 학년 문자열';

-- ============================================================
-- 7. regulations
-- ============================================================
CREATE TABLE regulations (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    major           VARCHAR(100),
    title           VARCHAR(300) NOT NULL,
    content         TEXT         NOT NULL,
    source_tag      VARCHAR(100),
    content_vector  TSVECTOR     GENERATED ALWAYS AS (
                        to_tsvector('simple', title || ' ' || content)
                    ) STORED,
    embedding       VECTOR(1536),
    effective_date  DATE         NOT NULL,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_reg_fts ON regulations USING GIN(content_vector);
-- 의미검색용 임베딩은 regulations_seeder.backfill_embeddings()가 채운다.
-- 행이 수십 건 규모면 인덱스 없이도 코사인 정렬(embedding <=> q)이 충분히 빠르다.
-- 대량 적재 후 성능이 필요하면 backfill 완료 뒤 아래 인덱스를 생성한다(lists는 데이터 규모에 맞춰 조정):
-- CREATE INDEX idx_reg_vec ON regulations USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

COMMENT ON TABLE regulations IS 'AI Agent RAG 검색 대상 학칙·규정 원문';
COMMENT ON COLUMN regulations.embedding IS 'OpenAI / 사내 임베딩 모델 벡터 (pgvector 확장 필요)';

-- ============================================================
-- 8. transcript_uploads
-- ============================================================
CREATE TABLE transcript_uploads (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id      VARCHAR(12)  NOT NULL REFERENCES students(student_id) ON DELETE CASCADE,
    file_name       VARCHAR(255) NOT NULL,
    storage_path    TEXT         NOT NULL,
    parse_status    VARCHAR(20)  NOT NULL DEFAULT 'pending'
                        CHECK (parse_status IN ('pending','processing','done','failed')),
    parse_error     TEXT,
    uploaded_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    parsed_at       TIMESTAMPTZ
);

COMMENT ON TABLE transcript_uploads IS 'Student.uploadTranscript() 호출 이력 및 OCR 파싱 상태 추적';

-- ============================================================
-- Utility triggers
-- ============================================================
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;

CREATE TRIGGER trg_students_updated_at
    BEFORE UPDATE ON students
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_graduation_requirements_updated_at
    BEFORE UPDATE ON graduation_requirements
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
