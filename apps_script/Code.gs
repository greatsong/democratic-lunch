function doGet(e) {
  const sh = SpreadsheetApp.getActiveSheet();
  if (e.parameter.member) {                    // 값이 붙어 오면 → 시트에 저장
    const now = Utilities.formatDate(new Date(), "Asia/Seoul", "yyyy-MM-dd HH:mm:ss");
    sh.appendRow([now, e.parameter.member, e.parameter.menu, e.parameter.type]);
    return ContentService.createTextOutput("OK");
  }
  const rows = sh.getDataRange().getValues();  // 안 붙어 오면 → 전체 기록 돌려주기
  return ContentService.createTextOutput(JSON.stringify(rows))
                       .setMimeType(ContentService.MimeType.JSON);
}