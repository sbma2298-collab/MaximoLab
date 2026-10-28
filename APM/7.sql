/* =====================================================================
   MaximoLab - APM KPI layer  (ALL-IN-ONE, run once in SSMS, safe to re-run)
   ---------------------------------------------------------------------
   Creates schema [apm]:
     apm.vw_AssetBase           raw facts per asset
     apm.vw_AHI                 Asset Health Index (APM health)
     apm.vw_ACI                 Asset Criticality Index
     apm.vw_DCI                 Data Completeness Index (per check)
     apm.DQ_ALLOWED_VALUES      reference lists for validity rules (editable)
     apm.vw_DQ_Asset            5 data-quality dimensions per asset
     apm.vw_ADQS                ADQS = average of the 5 dimensions
     apm.vw_AIP                 Asset Investment Planning (risk + action)
     apm.vw_KPI_Summary         headline numbers
     apm.vw_DQ_DimensionSummary one row per DQ dimension
     apm.vw_HC_Quadrant         Health x Criticality quadrant per asset
     apm.vw_HC_QuadrantSummary  one row per quadrant

   Assumptions (edit if your data differs):
     WORKTYPE 'CM','EM' = corrective/emergency; closed WO = 'COMP','CLOSE','CAN'
     PRIORITY 1 = most critical; Poor health AHI < 60; High criticality ACI >= 60
   ===================================================================== */
USE MaximoLab;
GO
SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO
IF SCHEMA_ID('apm') IS NULL EXEC('CREATE SCHEMA apm');
GO

/* 1. BASE ------------------------------------------------------------ */
CREATE OR ALTER VIEW apm.vw_AssetBase AS
WITH meter AS (
    SELECT am.ASSETNUM, COUNT(DISTINCT am.ASSETMETERID) AS MeterCount,
           COUNT(ms.MEASUREMENTID) AS ReadingCount, MAX(ms.MEASUREDATE) AS LastReadingDate
    FROM lab.ASSETMETER am
    LEFT JOIN lab.MEASUREMENT ms ON ms.ASSETMETERID = am.ASSETMETERID
    GROUP BY am.ASSETNUM
),
meterdev AS (
    SELECT x.ASSETNUM, AVG(x.DevPct) AS AvgDeviationPct
    FROM (
        SELECT am.ASSETNUM,
               ABS(lastv.MEASUREMENTVALUE - st.AvgVal) * 100.0 / NULLIF(ABS(st.AvgVal), 0) AS DevPct
        FROM lab.ASSETMETER am
        CROSS APPLY (SELECT AVG(CAST(m.MEASUREMENTVALUE AS float)) AS AvgVal, COUNT(*) AS Cnt
                     FROM lab.MEASUREMENT m WHERE m.ASSETMETERID = am.ASSETMETERID) st
        CROSS APPLY (SELECT TOP 1 m.MEASUREMENTVALUE FROM lab.MEASUREMENT m
                     WHERE m.ASSETMETERID = am.ASSETMETERID ORDER BY m.MEASUREDATE DESC) lastv
        WHERE st.Cnt >= 2
    ) x
    GROUP BY x.ASSETNUM
),
pm AS (
    SELECT ASSETNUM, COUNT(*) AS PMCount,
           SUM(CASE WHEN NEXTDATE < CAST(GETDATE() AS date) THEN 1 ELSE 0 END) AS PMOverdue
    FROM lab.PM GROUP BY ASSETNUM
),
wo AS (
    SELECT ASSETNUM, COUNT(*) AS WOCount,
           SUM(CASE WHEN WORKTYPE IN ('CM','EM') THEN 1 ELSE 0 END) AS CorrectiveWO,
           SUM(CASE WHEN STATUS NOT IN ('COMP','CLOSE','CAN') THEN 1 ELSE 0 END) AS OpenWO
    FROM lab.WORKORDER WHERE ASSETNUM IS NOT NULL GROUP BY ASSETNUM
),
labcost AS (
    SELECT w.ASSETNUM, SUM(lt.REGULARHRS * lt.PAYRATE) AS LaborCost
    FROM lab.LABTRANS lt JOIN lab.WORKORDER w ON w.WONUM = lt.WONUM
    WHERE w.ASSETNUM IS NOT NULL GROUP BY w.ASSETNUM
),
matcost AS (
    SELECT w.ASSETNUM, SUM(mu.QUANTITY * mu.UNITCOST) AS MaterialCost
    FROM lab.MATUSETRANS mu JOIN lab.WORKORDER w ON w.WONUM = mu.WONUM
    WHERE w.ASSETNUM IS NOT NULL GROUP BY w.ASSETNUM
)
SELECT a.ASSETNUM, a.SITEID, a.LOCATION, a.DESCRIPTION, a.STATUS, a.PRIORITY,
       CASE WHEN l.SITEID = a.SITEID THEN 1 ELSE 0 END AS LocationSiteMatch,
       ISNULL(m.MeterCount, 0) AS MeterCount, ISNULL(m.ReadingCount, 0) AS ReadingCount,
       m.LastReadingDate, md.AvgDeviationPct,
       ISNULL(p.PMCount, 0) AS PMCount, ISNULL(p.PMOverdue, 0) AS PMOverdue,
       ISNULL(w.WOCount, 0) AS WOCount, ISNULL(w.CorrectiveWO, 0) AS CorrectiveWO,
       ISNULL(w.OpenWO, 0) AS OpenWO,
       CAST(ISNULL(lc.LaborCost, 0) + ISNULL(mc.MaterialCost, 0) AS decimal(14,2)) AS MaintCost
FROM lab.ASSET a
LEFT JOIN lab.LOCATIONS l ON l.LOCATION = a.LOCATION
LEFT JOIN meter m     ON m.ASSETNUM  = a.ASSETNUM
LEFT JOIN meterdev md ON md.ASSETNUM = a.ASSETNUM
LEFT JOIN pm p        ON p.ASSETNUM  = a.ASSETNUM
LEFT JOIN wo w        ON w.ASSETNUM  = a.ASSETNUM
LEFT JOIN labcost lc  ON lc.ASSETNUM = a.ASSETNUM
LEFT JOIN matcost mc  ON mc.ASSETNUM = a.ASSETNUM;
GO

/* 2. AHI - health: 35% failures + 25% PM compliance + 25% condition + 15% backlog */
CREATE OR ALTER VIEW apm.vw_AHI AS
SELECT b.ASSETNUM, b.SITEID, b.LOCATION, b.DESCRIPTION,
       b.CorrectiveWO, b.PMCount, b.PMOverdue, b.OpenWO,
       s.FailureScore, s.PMScore, s.ConditionScore, s.BacklogScore, h.AHI,
       CASE WHEN h.AHI >= 80 THEN 'Good' WHEN h.AHI >= 60 THEN 'Fair'
            WHEN h.AHI >= 40 THEN 'Poor' ELSE 'Critical' END AS HealthBand
FROM apm.vw_AssetBase b
CROSS APPLY (SELECT
    FailureScore   = CAST(100 - CASE WHEN b.CorrectiveWO * 15 > 100 THEN 100 ELSE b.CorrectiveWO * 15 END AS decimal(5,1)),
    PMScore        = CAST(CASE WHEN b.PMCount = 0 THEN 50 ELSE 100.0 * (b.PMCount - b.PMOverdue) / b.PMCount END AS decimal(5,1)),
    ConditionScore = CAST(CASE WHEN b.AvgDeviationPct IS NULL THEN 50 WHEN b.AvgDeviationPct >= 100 THEN 0
                               ELSE 100 - b.AvgDeviationPct END AS decimal(5,1)),
    BacklogScore   = CAST(100 - CASE WHEN b.OpenWO * 10 > 100 THEN 100 ELSE b.OpenWO * 10 END AS decimal(5,1))
) s
CROSS APPLY (SELECT AHI = CAST(0.35*s.FailureScore + 0.25*s.PMScore + 0.25*s.ConditionScore + 0.15*s.BacklogScore AS decimal(5,1))) h;
GO

/* 3. ACI - criticality: 50% priority + 30% cost + 20% failure frequency */
CREATE OR ALTER VIEW apm.vw_ACI AS
WITH mx AS (SELECT MAX(MaintCost) AS MaxCost, MAX(CorrectiveWO) AS MaxCM FROM apm.vw_AssetBase)
SELECT b.ASSETNUM, b.SITEID, b.LOCATION, b.DESCRIPTION, b.PRIORITY, b.MaintCost, b.CorrectiveWO,
       s.PriorityScore, s.CostScore, s.FrequencyScore, c.ACI,
       CASE WHEN c.ACI >= 60 THEN 'High' WHEN c.ACI >= 40 THEN 'Medium' ELSE 'Low' END AS CriticalityBand
FROM apm.vw_AssetBase b
CROSS JOIN mx
CROSS APPLY (SELECT
    PriorityScore  = CAST((6 - b.PRIORITY) * 20 AS decimal(5,1)),
    CostScore      = CAST(ISNULL(100.0 * b.MaintCost    / NULLIF(mx.MaxCost, 0), 0) AS decimal(5,1)),
    FrequencyScore = CAST(ISNULL(100.0 * b.CorrectiveWO / NULLIF(mx.MaxCM,   0), 0) AS decimal(5,1))
) s
CROSS APPLY (SELECT ACI = CAST(0.5*s.PriorityScore + 0.3*s.CostScore + 0.2*s.FrequencyScore AS decimal(5,1))) c;
GO

/* 4. DCI - database-wide completeness checks */
CREATE OR ALTER VIEW apm.vw_DCI AS
SELECT Area, CheckName, TotalRows, ISNULL(FailRows, 0) AS FailRows,
       CAST(CASE WHEN TotalRows = 0 THEN NULL
                 ELSE 100.0 * (TotalRows - ISNULL(FailRows, 0)) / TotalRows END AS decimal(5,1)) AS CompletenessPct
FROM (
    SELECT 'Asset' AS Area, 'Description filled' AS CheckName, COUNT(*) AS TotalRows,
           SUM(CASE WHEN NULLIF(LTRIM(RTRIM(DESCRIPTION)), N'') IS NULL THEN 1 ELSE 0 END) AS FailRows
    FROM lab.ASSET
    UNION ALL
    SELECT 'Asset', 'Location belongs to asset site', COUNT(*), SUM(CASE WHEN l.SITEID = a.SITEID THEN 0 ELSE 1 END)
    FROM lab.ASSET a LEFT JOIN lab.LOCATIONS l ON l.LOCATION = a.LOCATION
    UNION ALL
    SELECT 'Asset', 'Has at least one meter', COUNT(*), SUM(CASE WHEN m.ASSETNUM IS NULL THEN 1 ELSE 0 END)
    FROM lab.ASSET a LEFT JOIN (SELECT DISTINCT ASSETNUM FROM lab.ASSETMETER) m ON m.ASSETNUM = a.ASSETNUM
    UNION ALL
    SELECT 'Asset', 'Covered by a PM', COUNT(*), SUM(CASE WHEN p.ASSETNUM IS NULL THEN 1 ELSE 0 END)
    FROM lab.ASSET a LEFT JOIN (SELECT DISTINCT ASSETNUM FROM lab.PM) p ON p.ASSETNUM = a.ASSETNUM
    UNION ALL
    SELECT 'Meter', 'Meter has readings', COUNT(*), SUM(CASE WHEN r.ASSETMETERID IS NULL THEN 1 ELSE 0 END)
    FROM lab.ASSETMETER am LEFT JOIN (SELECT DISTINCT ASSETMETERID FROM lab.MEASUREMENT) r ON r.ASSETMETERID = am.ASSETMETERID
    UNION ALL
    SELECT 'Meter', 'Reading in last 180 days', COUNT(*),
           SUM(CASE WHEN r.LastDate IS NULL OR r.LastDate < DATEADD(DAY, -180, GETDATE()) THEN 1 ELSE 0 END)
    FROM lab.ASSETMETER am
    LEFT JOIN (SELECT ASSETMETERID, MAX(MEASUREDATE) AS LastDate FROM lab.MEASUREMENT GROUP BY ASSETMETERID) r
           ON r.ASSETMETERID = am.ASSETMETERID
    UNION ALL
    SELECT 'Work Order', 'Asset linked', COUNT(*), SUM(CASE WHEN ASSETNUM IS NULL THEN 1 ELSE 0 END) FROM lab.WORKORDER
    UNION ALL
    SELECT 'Work Order', 'Location filled', COUNT(*), SUM(CASE WHEN LOCATION IS NULL THEN 1 ELSE 0 END) FROM lab.WORKORDER
    UNION ALL
    SELECT 'Work Order', 'Description filled', COUNT(*),
           SUM(CASE WHEN NULLIF(LTRIM(RTRIM(DESCRIPTION)), N'') IS NULL THEN 1 ELSE 0 END) FROM lab.WORKORDER
    UNION ALL
    SELECT 'Work Order', 'WO location matches asset location', COUNT(*),
           SUM(CASE WHEN w.LOCATION IS NULL OR w.LOCATION <> a.LOCATION THEN 1 ELSE 0 END)
    FROM lab.WORKORDER w JOIN lab.ASSET a ON a.ASSETNUM = w.ASSETNUM
    UNION ALL
    SELECT 'Work Order', 'PM work order linked to PM', COUNT(*), SUM(CASE WHEN PMNUM IS NULL THEN 1 ELSE 0 END)
    FROM lab.WORKORDER WHERE WORKTYPE = 'PM'
    UNION ALL
    SELECT 'Work Order', 'PM work order has job plan', COUNT(*), SUM(CASE WHEN JPNUM IS NULL THEN 1 ELSE 0 END)
    FROM lab.WORKORDER WHERE WORKTYPE = 'PM'
    UNION ALL
    SELECT 'Work Order', 'Completed WO has labor actuals', COUNT(*), SUM(CASE WHEN lt.WONUM IS NULL THEN 1 ELSE 0 END)
    FROM lab.WORKORDER w LEFT JOIN (SELECT DISTINCT WONUM FROM lab.LABTRANS) lt ON lt.WONUM = w.WONUM
    WHERE w.STATUS IN ('COMP','CLOSE')
    UNION ALL
    SELECT 'Work Order', 'Completed WO has ACTLABHRS > 0', COUNT(*), SUM(CASE WHEN ACTLABHRS = 0 THEN 1 ELSE 0 END)
    FROM lab.WORKORDER WHERE STATUS IN ('COMP','CLOSE')
    UNION ALL
    SELECT 'Work Order', 'Has status history', COUNT(*), SUM(CASE WHEN s.WONUM IS NULL THEN 1 ELSE 0 END)
    FROM lab.WORKORDER w LEFT JOIN (SELECT DISTINCT WONUM FROM lab.WOSTATUS) s ON s.WONUM = w.WONUM
    UNION ALL
    SELECT 'Reference', 'Location description filled', COUNT(*),
           SUM(CASE WHEN NULLIF(LTRIM(RTRIM(DESCRIPTION)), N'') IS NULL THEN 1 ELSE 0 END) FROM lab.LOCATIONS
    UNION ALL
    SELECT 'Reference', 'Item description filled', COUNT(*),
           SUM(CASE WHEN NULLIF(LTRIM(RTRIM(DESCRIPTION)), N'') IS NULL THEN 1 ELSE 0 END) FROM lab.ITEM
    UNION ALL
    SELECT 'Reference', 'Job plan description filled', COUNT(*),
           SUM(CASE WHEN NULLIF(LTRIM(RTRIM(DESCRIPTION)), N'') IS NULL THEN 1 ELSE 0 END) FROM lab.JOBPLAN
    UNION ALL
    SELECT 'Reference', 'Person display name filled', COUNT(*),
           SUM(CASE WHEN NULLIF(LTRIM(RTRIM(DISPLAYNAME)), N'') IS NULL THEN 1 ELSE 0 END) FROM lab.PERSON
) x;
GO

/* ---------------------------------------------------------------------
   0. Reference lists for VALIDITY rules  -> edit these to match your data
      Check your real codes with:
        SELECT DISTINCT STATUS FROM lab.ASSET;
        SELECT DISTINCT MEASUREUNIT FROM lab.ASSETMETER;
   --------------------------------------------------------------------- */
IF OBJECT_ID('apm.DQ_ALLOWED_VALUES') IS NULL
BEGIN
    CREATE TABLE apm.DQ_ALLOWED_VALUES (
        DOMAIN varchar(30) NOT NULL,
        VALUE  varchar(30) NOT NULL,
        PRIMARY KEY (DOMAIN, VALUE)
    );
    INSERT INTO apm.DQ_ALLOWED_VALUES (DOMAIN, VALUE) VALUES
    ('ASSETSTATUS','OPERATING'),('ASSETSTATUS','ACTIVE'),('ASSETSTATUS','NOT READY'),
    ('ASSETSTATUS','LIMITEDUSE'),('ASSETSTATUS','BROKEN'),('ASSETSTATUS','INACTIVE'),
    ('ASSETSTATUS','DECOMMISSIONED'),
    ('INACTIVESTATUS','INACTIVE'),('INACTIVESTATUS','DECOMMISSIONED'),
    ('MEASUREUNIT','HOURS'),('MEASUREUNIT','HRS'),('MEASUREUNIT','KM'),('MEASUREUNIT','MILES'),
    ('MEASUREUNIT','DEG C'),('MEASUREUNIT','DEGC'),('MEASUREUNIT','C'),('MEASUREUNIT','F'),
    ('MEASUREUNIT','PSI'),('MEASUREUNIT','KPA'),('MEASUREUNIT','BAR'),('MEASUREUNIT','KV'),
    ('MEASUREUNIT','V'),('MEASUREUNIT','A'),('MEASUREUNIT','AMPS'),('MEASUREUNIT','KW'),
    ('MEASUREUNIT','KWH'),('MEASUREUNIT','MW'),('MEASUREUNIT','MVA'),('MEASUREUNIT','PPM'),
    ('MEASUREUNIT','%'),('MEASUREUNIT','MM/S'),('MEASUREUNIT','CYCLES'),('MEASUREUNIT','COUNT'),
    ('MEASUREUNIT','OPS'),('MEASUREUNIT','EACH');
END
GO

/* ---------------------------------------------------------------------
   1. Five data-quality dimensions per asset
   --------------------------------------------------------------------- */
CREATE OR ALTER VIEW apm.vw_DQ_Asset AS
WITH rd AS (            -- every reading with its meter statistics
    SELECT am.ASSETNUM, am.ASSETMETERID, am.LASTREADING,
           m.MEASUREMENTVALUE AS v, m.MEASUREDATE AS d,
           AVG(CAST(m.MEASUREMENTVALUE AS float))   OVER (PARTITION BY m.ASSETMETERID) AS mu,
           STDEV(CAST(m.MEASUREMENTVALUE AS float)) OVER (PARTITION BY m.ASSETMETERID) AS sd,
           COUNT(*) OVER (PARTITION BY m.ASSETMETERID) AS n,
           ROW_NUMBER() OVER (PARTITION BY m.ASSETMETERID
                              ORDER BY m.MEASUREDATE DESC, m.MEASUREMENTID DESC) AS rn
    FROM lab.ASSETMETER am
    JOIN lab.MEASUREMENT m ON m.ASSETMETERID = am.ASSETMETERID
),
readagg AS (
    SELECT ASSETNUM,
           COUNT(*) AS ReadCnt,
           SUM(CASE WHEN v < 0 THEN 1 ELSE 0 END)                         AS NegCnt,
           SUM(CASE WHEN d > SYSDATETIME() THEN 1 ELSE 0 END)             AS FutureCnt,
           SUM(CASE WHEN n >= 5 AND sd > 0 AND ABS(v - mu) > 3 * sd
                    THEN 1 ELSE 0 END)                                    AS OutlierCnt,
           SUM(CASE WHEN rn = 1 AND v <> LASTREADING THEN 1 ELSE 0 END)   AS LastMismatch,
           MAX(CASE WHEN d <= SYSDATETIME() THEN d END)                   AS LastDate
    FROM rd
    GROUP BY ASSETNUM
),
meteragg AS (
    SELECT am.ASSETNUM,
           COUNT(*) AS MeterCnt,
           SUM(CASE WHEN u.VALUE IS NULL THEN 1 ELSE 0 END) AS BadUnit
    FROM lab.ASSETMETER am
    LEFT JOIN apm.DQ_ALLOWED_VALUES u
           ON u.DOMAIN = 'MEASUREUNIT' AND u.VALUE = UPPER(LTRIM(RTRIM(am.MEASUREUNIT)))
    GROUP BY am.ASSETNUM
),
woagg AS (
    SELECT w.ASSETNUM,
           COUNT(*) AS WOCnt,
           SUM(CASE WHEN w.REPORTDATE > SYSDATETIME() THEN 1 ELSE 0 END)                 AS FutureWO,
           SUM(CASE WHEN w.SITEID <> a.SITEID THEN 1 ELSE 0 END)                         AS SiteMis,
           SUM(CASE WHEN w.LOCATION IS NOT NULL AND w.LOCATION <> a.LOCATION THEN 1 ELSE 0 END) AS LocMis,
           SUM(CASE WHEN w.STATUS NOT IN ('COMP','CLOSE','CAN') THEN 1 ELSE 0 END)       AS OpenWO,
           MAX(CASE WHEN w.REPORTDATE <= SYSDATETIME() THEN w.REPORTDATE END)            AS LastWO
    FROM lab.WORKORDER w
    JOIN lab.ASSET a ON a.ASSETNUM = w.ASSETNUM
    GROUP BY w.ASSETNUM
),
labacc AS (             -- completed WOs: header hours vs labour transactions (10% tolerance)
    SELECT w.ASSETNUM,
           COUNT(*) AS Checked,
           SUM(CASE WHEN ABS(w.ACTLABHRS - ISNULL(lt.Hrs, 0))
                         > 0.1 * CASE WHEN w.ACTLABHRS > ISNULL(lt.Hrs, 0)
                                      THEN w.ACTLABHRS ELSE ISNULL(lt.Hrs, 0) END + 0.01
                    THEN 1 ELSE 0 END) AS Mismatch
    FROM lab.WORKORDER w
    LEFT JOIN (SELECT WONUM, SUM(REGULARHRS) AS Hrs FROM lab.LABTRANS GROUP BY WONUM) lt
           ON lt.WONUM = w.WONUM
    WHERE w.STATUS IN ('COMP','CLOSE') AND w.ASSETNUM IS NOT NULL
    GROUP BY w.ASSETNUM
),
pmagg AS (
    SELECT ASSETNUM,
           COUNT(*) AS PMCnt,
           SUM(CASE WHEN NEXTDATE < DATEADD(DAY, -30, CAST(GETDATE() AS date)) THEN 1 ELSE 0 END) AS PMLate
    FROM lab.PM
    GROUP BY ASSETNUM
),
rules AS (
    SELECT a.ASSETNUM, a.SITEID, a.LOCATION, a.DESCRIPTION, a.STATUS,
        /* ---- COMPLETENESS: is the data there? ---- */
        C_Desc     = CASE WHEN NULLIF(LTRIM(RTRIM(a.DESCRIPTION)), N'') IS NULL THEN 0 ELSE 1 END,
        C_Meter    = CASE WHEN ma.MeterCnt IS NULL THEN 0 ELSE 1 END,
        C_Reading  = CASE WHEN ma.MeterCnt IS NULL THEN NULL WHEN ra.ReadCnt IS NULL THEN 0 ELSE 1 END,
        C_PM       = CASE WHEN pm.PMCnt IS NULL THEN 0 ELSE 1 END,
        C_WOHist   = CASE WHEN wo.WOCnt IS NULL THEN 0 ELSE 1 END,
        /* ---- VALIDITY: does it follow the rules / allowed values? ---- */
        V_Priority = CASE WHEN a.PRIORITY BETWEEN 1 AND 5 THEN 1 ELSE 0 END,
        V_Status   = CASE WHEN st.VALUE IS NULL THEN 0 ELSE 1 END,
        V_Unit     = CASE WHEN ma.MeterCnt IS NULL THEN NULL WHEN ma.BadUnit = 0 THEN 1 ELSE 0 END,
        V_Range    = CASE WHEN ra.ReadCnt IS NULL THEN NULL WHEN ra.NegCnt = 0 THEN 1 ELSE 0 END,
        V_Dates    = CASE WHEN ra.ReadCnt IS NULL AND wo.WOCnt IS NULL THEN NULL
                          WHEN ISNULL(ra.FutureCnt, 0) + ISNULL(wo.FutureWO, 0) = 0 THEN 1 ELSE 0 END,
        /* ---- ACCURACY (proxy - true accuracy needs field verification) ---- */
        A_LastRead = CASE WHEN ra.ReadCnt IS NULL THEN NULL WHEN ra.LastMismatch = 0 THEN 1 ELSE 0 END,
        A_Outlier  = CASE WHEN ra.ReadCnt IS NULL THEN NULL WHEN ra.OutlierCnt = 0 THEN 1 ELSE 0 END,
        A_LabHrs   = CASE WHEN la.Checked IS NULL THEN NULL WHEN la.Mismatch = 0 THEN 1 ELSE 0 END,
        /* ---- CONSISTENCY: does the same fact agree across tables? ---- */
        S_LocSite  = CASE WHEN l.SITEID = a.SITEID THEN 1 ELSE 0 END,
        S_WOSite   = CASE WHEN wo.WOCnt IS NULL THEN NULL WHEN wo.SiteMis = 0 THEN 1 ELSE 0 END,
        S_WOLoc    = CASE WHEN wo.WOCnt IS NULL THEN NULL WHEN wo.LocMis = 0 THEN 1 ELSE 0 END,
        S_StatAct  = CASE WHEN ina.VALUE IS NULL THEN 1
                          WHEN ISNULL(wo.OpenWO, 0) + ISNULL(pm.PMCnt, 0) = 0 THEN 1 ELSE 0 END,
        /* ---- TIMELINESS: is it recent enough to trust? ---- */
        T_Reading  = CASE WHEN ma.MeterCnt IS NULL THEN NULL
                          WHEN ra.LastDate >= DATEADD(DAY, -180, SYSDATETIME()) THEN 1 ELSE 0 END,
        T_PM       = CASE WHEN pm.PMCnt IS NULL THEN NULL WHEN pm.PMLate = 0 THEN 1 ELSE 0 END,
        T_WO       = CASE WHEN wo.WOCnt IS NULL THEN NULL
                          WHEN wo.LastWO >= DATEADD(DAY, -365, SYSDATETIME()) THEN 1 ELSE 0 END
    FROM lab.ASSET a
    LEFT JOIN lab.LOCATIONS l  ON l.LOCATION = a.LOCATION
    LEFT JOIN meteragg ma      ON ma.ASSETNUM = a.ASSETNUM
    LEFT JOIN readagg  ra      ON ra.ASSETNUM = a.ASSETNUM
    LEFT JOIN woagg    wo      ON wo.ASSETNUM = a.ASSETNUM
    LEFT JOIN labacc   la      ON la.ASSETNUM = a.ASSETNUM
    LEFT JOIN pmagg    pm      ON pm.ASSETNUM = a.ASSETNUM
    LEFT JOIN apm.DQ_ALLOWED_VALUES st  ON st.DOMAIN  = 'ASSETSTATUS'    AND st.VALUE  = UPPER(a.STATUS)
    LEFT JOIN apm.DQ_ALLOWED_VALUES ina ON ina.DOMAIN = 'INACTIVESTATUS' AND ina.VALUE = UPPER(a.STATUS)
)
SELECT r.*,
       d.Completeness, d.Validity, d.Accuracy, d.Consistency, d.Timeliness,
       s.ADQS,
       CASE WHEN s.ADQS >= 80 THEN 'Trusted' WHEN s.ADQS >= 60 THEN 'Usable'
            ELSE 'Fix data first' END AS ADQSBand,
       CONCAT(
         CASE WHEN r.C_Desc    = 0 THEN 'No description; '        END,
         CASE WHEN r.C_Meter   = 0 THEN 'No meter; '              END,
         CASE WHEN r.C_Reading = 0 THEN 'Meter has no readings; ' END,
         CASE WHEN r.C_PM      = 0 THEN 'No PM; '                 END,
         CASE WHEN r.C_WOHist  = 0 THEN 'No WO history; '         END,
         CASE WHEN r.V_Priority= 0 THEN 'Invalid priority; '      END,
         CASE WHEN r.V_Status  = 0 THEN 'Unknown status; '        END,
         CASE WHEN r.V_Unit    = 0 THEN 'Unknown meter unit; '    END,
         CASE WHEN r.V_Range   = 0 THEN 'Negative reading; '      END,
         CASE WHEN r.V_Dates   = 0 THEN 'Future-dated record; '   END,
         CASE WHEN r.A_LastRead= 0 THEN 'LASTREADING <> latest measurement; ' END,
         CASE WHEN r.A_Outlier = 0 THEN 'Outlier readings (>3 SD); '          END,
         CASE WHEN r.A_LabHrs  = 0 THEN 'WO hours <> labour actuals; '        END,
         CASE WHEN r.S_LocSite = 0 THEN 'Location in other site; '            END,
         CASE WHEN r.S_WOSite  = 0 THEN 'WO site <> asset site; '             END,
         CASE WHEN r.S_WOLoc   = 0 THEN 'WO location <> asset location; '     END,
         CASE WHEN r.S_StatAct = 0 THEN 'Inactive asset with open work/PM; '  END,
         CASE WHEN r.T_Reading = 0 THEN 'No reading in 180 days; '            END,
         CASE WHEN r.T_PM      = 0 THEN 'PM overdue > 30 days; '              END,
         CASE WHEN r.T_WO      = 0 THEN 'No WO in 365 days; '                 END
       ) AS FailedRules
FROM rules r
CROSS APPLY (SELECT
    Completeness = (SELECT CAST(AVG(CAST(x AS float)) * 100 AS decimal(5,1))
                    FROM (VALUES (r.C_Desc),(r.C_Meter),(r.C_Reading),(r.C_PM),(r.C_WOHist)) v(x)),
    Validity     = (SELECT CAST(AVG(CAST(x AS float)) * 100 AS decimal(5,1))
                    FROM (VALUES (r.V_Priority),(r.V_Status),(r.V_Unit),(r.V_Range),(r.V_Dates)) v(x)),
    Accuracy     = (SELECT CAST(AVG(CAST(x AS float)) * 100 AS decimal(5,1))
                    FROM (VALUES (r.A_LastRead),(r.A_Outlier),(r.A_LabHrs)) v(x)),
    Consistency  = (SELECT CAST(AVG(CAST(x AS float)) * 100 AS decimal(5,1))
                    FROM (VALUES (r.S_LocSite),(r.S_WOSite),(r.S_WOLoc),(r.S_StatAct)) v(x)),
    Timeliness   = (SELECT CAST(AVG(CAST(x AS float)) * 100 AS decimal(5,1))
                    FROM (VALUES (r.T_Reading),(r.T_PM),(r.T_WO)) v(x))
) d
CROSS APPLY (SELECT ADQS = (SELECT CAST(ISNULL(AVG(CAST(x AS float)), 0) AS decimal(5,1))
                            FROM (VALUES (d.Completeness),(d.Validity),(d.Accuracy),
                                         (d.Consistency),(d.Timeliness)) v(x))) s;
GO

/* ---------------------------------------------------------------------
   2. ADQS replaced with the 5-dimension version (same column names as v1,
      so apm.vw_AIP and the dashboard keep working)
   --------------------------------------------------------------------- */
CREATE OR ALTER VIEW apm.vw_ADQS AS
SELECT ASSETNUM, SITEID, LOCATION, DESCRIPTION,
       C_Desc                AS HasDescription,
       S_LocSite             AS LocationValid,
       C_Meter               AS HasMeter,
       ISNULL(T_Reading, 0)  AS RecentReading,
       C_PM                  AS HasPM,
       C_WOHist              AS HasWOHistory,
       Completeness, Validity, Accuracy, Consistency, Timeliness,
       ADQS, ADQSBand, FailedRules
FROM apm.vw_DQ_Asset;
GO

/* AIP - investment planning: Risk = (100 - AHI) x ACI / 100 */
CREATE OR ALTER VIEW apm.vw_AIP AS
SELECT h.ASSETNUM, h.SITEID, h.LOCATION, h.DESCRIPTION,
       h.AHI, h.HealthBand, c.ACI, c.CriticalityBand, q.ADQS, c.MaintCost, r.RiskScore,
       RANK() OVER (ORDER BY r.RiskScore DESC) AS RiskRank,
       CASE WHEN h.AHI < 40 AND c.ACI >= 60 THEN 'Replace'
            WHEN h.AHI < 60                  THEN 'Refurbish / Overhaul'
            WHEN h.AHI < 80 AND c.ACI >= 60  THEN 'Increase inspection'
            ELSE 'Run & monitor' END AS Action,
       CASE WHEN h.AHI < 40 AND c.ACI >= 60 THEN 'Year 1'
            WHEN h.AHI < 60                  THEN 'Year 1-2'
            WHEN h.AHI < 80 AND c.ACI >= 60  THEN 'Year 2-3'
            ELSE 'Year 3-5' END AS Horizon,
       CASE WHEN q.ADQS >= 80 THEN 'High' WHEN q.ADQS >= 60 THEN 'Medium' ELSE 'Low' END AS Confidence
FROM apm.vw_AHI h
JOIN apm.vw_ACI  c ON c.ASSETNUM = h.ASSETNUM
JOIN apm.vw_ADQS q ON q.ASSETNUM = h.ASSETNUM
CROSS APPLY (SELECT RiskScore = CAST((100 - h.AHI) * c.ACI / 100.0 AS decimal(5,1))) r;
GO

/* KPI summary */
CREATE OR ALTER VIEW apm.vw_KPI_Summary AS
SELECT
    (SELECT COUNT(*) FROM lab.ASSET)                                                AS AssetCount,
    (SELECT CAST(AVG(CAST(ADQS AS decimal(6,1))) AS decimal(5,1)) FROM apm.vw_ADQS)  AS AvgADQS,
    (SELECT CAST(AVG(AHI) AS decimal(5,1)) FROM apm.vw_AHI)                          AS AvgAHI,
    (SELECT CAST(AVG(ACI) AS decimal(5,1)) FROM apm.vw_ACI)                          AS AvgACI,
    (SELECT CAST(AVG(CompletenessPct) AS decimal(5,1)) FROM apm.vw_DCI)              AS DCI,
    (SELECT COUNT(*) FROM apm.vw_AHI WHERE HealthBand IN ('Poor','Critical'))        AS PoorOrCriticalAssets,
    (SELECT COUNT(*) FROM apm.vw_AIP WHERE Action IN ('Replace','Refurbish / Overhaul')) AS AssetsNeedingInvestment;
GO

/* ---------------------------------------------------------------------
   3. Portfolio summary per dimension
   --------------------------------------------------------------------- */
CREATE OR ALTER VIEW apm.vw_DQ_DimensionSummary AS
SELECT v.Dimension,
       CAST(AVG(CAST(v.Score AS float)) AS decimal(5,1))            AS AvgScore,
       SUM(CASE WHEN v.Score < 80 THEN 1 ELSE 0 END)                AS AssetsBelow80,
       COUNT(v.Score)                                               AS AssetsScored
FROM apm.vw_DQ_Asset a
CROSS APPLY (VALUES ('1 Completeness', a.Completeness), ('2 Validity', a.Validity),
                    ('3 Accuracy', a.Accuracy),         ('4 Consistency', a.Consistency),
                    ('5 Timeliness', a.Timeliness)) v(Dimension, Score)
GROUP BY v.Dimension;
GO

/* ---------------------------------------------------------------------
   4. Health x Criticality quadrant per asset
   --------------------------------------------------------------------- */
CREATE OR ALTER VIEW apm.vw_HC_Quadrant AS
SELECT h.ASSETNUM, h.SITEID, h.LOCATION, h.DESCRIPTION,
       h.AHI, c.ACI, q.ADQS, c.MaintCost,
       CASE WHEN h.AHI < 60  THEN 'Poor' ELSE 'Good' END AS HealthState,
       CASE WHEN c.ACI >= 60 THEN 'High' ELSE 'Low'  END AS CriticalityState,
       x.Quadrant, x.QuadrantName, x.Strategy, x.Approach,
       CASE WHEN q.ADQS < 60 THEN 'Verify data before acting' ELSE 'OK' END AS DataFlag
FROM apm.vw_AHI h
JOIN apm.vw_ACI  c ON c.ASSETNUM = h.ASSETNUM
JOIN apm.vw_ADQS q ON q.ASSETNUM = h.ASSETNUM
CROSS APPLY (SELECT
    Quadrant = CASE WHEN h.AHI < 60 AND c.ACI >= 60 THEN 'Q1'
                    WHEN h.AHI < 60                 THEN 'Q2'
                    WHEN c.ACI >= 60                THEN 'Q3'
                    ELSE 'Q4' END) k
CROSS APPLY (SELECT
    QuadrantName = CASE k.Quadrant
        WHEN 'Q1' THEN 'Poor health + High criticality'
        WHEN 'Q2' THEN 'Poor health + Low criticality'
        WHEN 'Q3' THEN 'Good health + High criticality'
        ELSE           'Good health + Low criticality' END,
    Strategy = CASE k.Quadrant
        WHEN 'Q1' THEN 'Act now'
        WHEN 'Q2' THEN 'Fix cheaply / run to failure'
        WHEN 'Q3' THEN 'Protect'
        ELSE           'Optimise' END,
    Approach = CASE k.Quadrant
        WHEN 'Q1' THEN 'Replace or refurbish in Year-1 capital plan; interim condition monitoring; hold critical spares'
        WHEN 'Q2' THEN 'Corrective maintenance; defer capex; bundle into a volume replacement programme'
        WHEN 'Q3' THEN 'Keep PM / condition-based maintenance and monitoring; secure spares; no capex needed'
        ELSE           'Extend PM intervals; reduce inspection effort; release budget to Q1' END,
    Quadrant = k.Quadrant
) x;
GO

/* ---------------------------------------------------------------------
   5. Quadrant summary
   --------------------------------------------------------------------- */
CREATE OR ALTER VIEW apm.vw_HC_QuadrantSummary AS
SELECT Quadrant, QuadrantName, Strategy,
       COUNT(*)                                       AS Assets,
       CAST(AVG(AHI)  AS decimal(5,1))                AS AvgAHI,
       CAST(AVG(ACI)  AS decimal(5,1))                AS AvgACI,
       CAST(AVG(CAST(ADQS AS float)) AS decimal(5,1)) AS AvgADQS,
       SUM(MaintCost)                                 AS MaintCost,
       SUM(CASE WHEN DataFlag <> 'OK' THEN 1 ELSE 0 END) AS AssetsNeedingDataCheck
FROM apm.vw_HC_Quadrant
GROUP BY Quadrant, QuadrantName, Strategy;
GO

/* ---------------------------------------------------------------------
   QUICK TEST
   --------------------------------------------------------------------- */
-- SELECT * FROM apm.vw_KPI_Summary;
-- SELECT * FROM apm.vw_DQ_DimensionSummary ORDER BY Dimension;
-- SELECT ASSETNUM, Completeness, Validity, Accuracy, Consistency, Timeliness, ADQS, FailedRules
--   FROM apm.vw_DQ_Asset ORDER BY ADQS;
-- SELECT * FROM apm.vw_HC_QuadrantSummary ORDER BY Quadrant;
-- SELECT * FROM apm.vw_HC_Quadrant WHERE Quadrant = 'Q1' ORDER BY AHI;
